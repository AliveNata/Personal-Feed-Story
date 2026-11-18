"""Subtitles for library videos: Groq Whisper hears the audio, Claude translates the lines.

Results are kept per blob, so the same video in two folders is transcribed once. Each track is
a JSON file of timed lines under library_dir/subtitles/<sha>/, the frontend draws them itself.
Job state lives in Redis like archive imports, with a TTL so a crashed worker never leaves a
video stuck on "running".
"""

import asyncio
import json
import logging
import math
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx
from redis import Redis
from redis.exceptions import RedisError

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

TranslationTarget = Literal["en", "id"]
Track = Literal["original", "en", "id"]
TRANSLATION_TARGETS: tuple[TranslationTarget, ...] = ("en", "id")

# Whisper writes text for music and silence too. Lines it rates as likely non speech are dropped.
NO_SPEECH_THRESHOLD = 0.6
# Lines per Claude request: enough context for natural phrasing, small enough to retry cheaply.
TRANSLATE_BATCH = 60
# Mono speech at this bitrate keeps an hour of audio near 11 MB, under Groq's 25 MB upload cap.
AUDIO_BITRATE = "24k"
GROQ_RETRIES = 4

# Whisper reports the language as a name or a code depending on the host, map both to ISO 639-1.
LANGUAGE_CODES = {
    "english": "en", "indonesian": "id", "malay": "ms", "javanese": "jw", "sundanese": "su",
    "japanese": "ja", "korean": "ko", "chinese": "zh", "thai": "th", "vietnamese": "vi",
    "tagalog": "tl", "hindi": "hi", "arabic": "ar", "spanish": "es", "portuguese": "pt",
    "french": "fr", "german": "de", "italian": "it", "dutch": "nl", "russian": "ru",
    "turkish": "tr", "polish": "pl", "ukrainian": "uk",
}  # fmt: skip
TARGET_NAMES: dict[TranslationTarget, str] = {
    "en": "natural, conversational English",
    "id": "natural, casual Bahasa Indonesia (as people actually talk, not stiff formal)",
}


class SubtitleError(Exception):
    """Shown to the user as is, so the message is in Indonesian."""


@dataclass(frozen=True)
class Line:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    language: str | None
    lines: list[Line]


def normalize_language(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip().lower()
    if len(value) == 2:
        return value
    return LANGUAGE_CODES.get(value)


class SubtitleFiles:
    def __init__(self, library_dir: Path) -> None:
        self.root = library_dir / "subtitles"

    def path(self, sha256: str, track: Track) -> Path:
        return self.root / sha256 / f"{track}.json"

    def has(self, sha256: str, track: Track) -> bool:
        return self.path(sha256, track).exists()

    def save(self, sha256: str, track: Track, transcript: Transcript) -> None:
        target = self.path(sha256, track)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"language": transcript.language, "lines": [asdict(x) for x in transcript.lines]}
        # Written beside and renamed, so a reader never sees half a file.
        partial = target.with_suffix(".part")
        partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        partial.replace(target)

    def load(self, sha256: str, track: Track) -> Transcript | None:
        path = self.path(sha256, track)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Transcript(raw.get("language"), [Line(**line) for line in raw["lines"]])

    def delete_all(self, sha256: str) -> None:
        shutil.rmtree(self.root / sha256, ignore_errors=True)


StateName = Literal["queued", "running", "failed"]
STATE_TTL_SECONDS = 6 * 60 * 60
FAILED_TTL_SECONDS = 60 * 60


class SubtitleStatus:
    """Queued, running or failed per blob and track. Done is the file on disk, not a state."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, sha256: str, track: Track) -> str:
        return f"subtitle:{sha256}:{track}"

    def set(
        self,
        sha256: str,
        track: Track,
        state: StateName,
        error: str | None = None,
        progress: int | None = None,
    ) -> None:
        ttl = FAILED_TTL_SECONDS if state == "failed" else STATE_TTL_SECONDS
        payload = {"state": state, "error": error, "progress": progress}
        try:
            self._redis.set(self._key(sha256, track), json.dumps(payload), ex=ttl)
        except RedisError as exc:
            logger.warning("could not store subtitle state: %s", exc)

    def get(self, sha256: str, track: Track) -> dict[str, Any] | None:
        try:
            raw = self._redis.get(self._key(sha256, track))
        except RedisError as exc:
            logger.warning("could not read subtitle state: %s", exc)
            return None
        return json.loads(raw) if raw else None  # type: ignore[arg-type]

    def clear(self, sha256: str, track: Track) -> None:
        try:
            self._redis.delete(self._key(sha256, track))
        except RedisError as exc:
            logger.warning("could not clear subtitle state: %s", exc)


# Audio


async def _run(*args: str) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return process.returncode or 0, stdout.decode(), stderr.decode()


async def media_duration(source: Path) -> float:
    code, out, err = await _run(
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(source)
    )
    if code != 0:
        logger.warning("ffprobe failed for %s: %s", source, err[-300:])
        raise SubtitleError("Video ini nggak bisa dibaca")
    try:
        return float(out.strip())
    except ValueError as exc:
        raise SubtitleError("Durasi video nggak kebaca") from exc


async def has_audio(source: Path) -> bool:
    code, out, _ = await _run(
        "ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
        "-of", "csv=p=0", str(source),
    )  # fmt: skip
    return code == 0 and bool(out.strip())


async def _audio_plan(source: Path, chunk_seconds: int) -> list[float]:
    """Offsets of the pieces the audio is sent in."""
    if not await has_audio(source):
        raise SubtitleError("Video ini nggak ada suaranya")
    duration = await media_duration(source)
    return [float(offset) for offset in range(0, max(math.ceil(duration), 1), chunk_seconds)]


async def _extract_chunk(source: Path, target: Path, offset: float, chunk_seconds: int) -> None:
    code, _, err = await _run(
        "ffmpeg", "-y", "-loglevel", "error", "-ss", f"{offset:.3f}", "-t", str(chunk_seconds),
        "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus",
        "-b:a", AUDIO_BITRATE, str(target),
    )  # fmt: skip
    if code != 0:
        logger.warning("audio extract failed at %.0fs: %s", offset, err[-300:])
        raise SubtitleError("Audio video ini gagal diambil")


async def extract_audio_chunks(
    source: Path, workdir: Path, chunk_seconds: int
) -> list[tuple[float, Path]]:
    """Speech friendly Opus pieces of the audio track, each with its offset in the video."""
    chunks: list[tuple[float, Path]] = []
    for index, offset in enumerate(await _audio_plan(source, chunk_seconds)):
        target = workdir / f"chunk-{index:03d}.ogg"
        await _extract_chunk(source, target, offset, chunk_seconds)
        chunks.append((offset, target))
    return chunks


# Speech to text


class Transcriber(Protocol):
    async def transcribe(self, audio: Path) -> tuple[str | None, list[Line]]: ...


class GroqTranscriber:
    def __init__(self, client: httpx.AsyncClient, api_key: str, model: str, base_url: str) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"

    async def transcribe(self, audio: Path) -> tuple[str | None, list[Line]]:
        if not self._api_key:
            raise SubtitleError("GROQ_API_KEY belum diisi di backend/.env")
        for attempt in range(GROQ_RETRIES):
            with audio.open("rb") as handle:
                response = await self._client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data={
                        "model": self._model,
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                        "temperature": "0",
                    },
                    files={"file": (audio.name, handle, "audio/ogg")},
                    timeout=300,
                )
            if response.status_code == 429 and attempt < GROQ_RETRIES - 1:
                wait = _retry_after(response, default=20 * (attempt + 1))
                logger.info("groq rate limited, waiting %.0fs", wait)
                await asyncio.sleep(wait)
                continue
            if response.status_code in (401, 403):
                raise SubtitleError("GROQ_API_KEY ditolak Groq, cek lagi key-nya")
            if response.status_code == 429:
                raise SubtitleError("Kuota Groq lagi habis, coba lagi nanti")
            if response.status_code >= 400:
                logger.warning("groq error %s: %s", response.status_code, response.text[:500])
                raise SubtitleError(f"Groq error {response.status_code}")
            body = response.json()
            return normalize_language(body.get("language")), _speech_lines(body.get("segments"))
        raise SubtitleError("Kuota Groq lagi habis, coba lagi nanti")


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return min(float(response.headers.get("retry-after", default)), 120.0)
    except ValueError:
        return default


def _speech_lines(segments: Any) -> list[Line]:
    lines: list[Line] = []
    for segment in segments or []:
        text = str(segment.get("text", "")).strip()
        if not text or float(segment.get("no_speech_prob", 0)) > NO_SPEECH_THRESHOLD:
            continue
        lines.append(Line(float(segment["start"]), float(segment["end"]), text))
    return lines


ProgressCallback = Callable[[int], None]


def _ignore_progress(_percent: int) -> None:
    return None


# Share of each piece's time spent cutting the audio, the rest is the upload and Groq.
EXTRACT_SHARE = 0.3


async def transcribe_video(
    source: Path,
    transcriber: Transcriber,
    chunk_seconds: int,
    on_progress: ProgressCallback = _ignore_progress,
) -> Transcript:
    offsets = await _audio_plan(source, chunk_seconds)
    lines: list[Line] = []
    languages: list[str] = []

    def report(done: float) -> None:
        # Capped below 100: the file is still to be written.
        on_progress(min(int(done / len(offsets) * 100), 99))

    with tempfile.TemporaryDirectory(prefix="subtitle-") as tmp:
        for index, offset in enumerate(offsets):
            path = Path(tmp) / f"chunk-{index:03d}.ogg"
            await _extract_chunk(source, path, offset, chunk_seconds)
            report(index + EXTRACT_SHARE)
            language, chunk_lines = await transcriber.transcribe(path)
            path.unlink(missing_ok=True)
            if language and chunk_lines:
                languages.append(language)
            lines.extend(Line(x.start + offset, x.end + offset, x.text) for x in chunk_lines)
            report(index + 1)
    # The language heard most across the pieces, not the first piece, which may be an intro song.
    language = max(set(languages), key=languages.count) if languages else None
    return Transcript(language, lines)


# Translation


class Translator(Protocol):
    async def translate(self, texts: list[str], target: TranslationTarget) -> list[str]: ...


def translation_prompt(texts: list[str], target: TranslationTarget) -> str:
    return (
        f"Translate these video subtitle lines into {TARGET_NAMES[target]}. "
        "Keep each line short like a subtitle and keep names as they are. "
        "Reply with only a JSON array of strings, one per input line, same order and the "
        f"same count ({len(texts)}).\n\n" + json.dumps(texts, ensure_ascii=False)
    )


class GroqTranslator:
    """A text model on Groq, the same key and free tier as the transcription."""

    def __init__(self, client: httpx.AsyncClient, api_key: str, model: str, base_url: str) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"

    async def translate(self, texts: list[str], target: TranslationTarget) -> list[str]:
        if not self._api_key:
            raise SubtitleError("GROQ_API_KEY belum diisi di backend/.env")
        for attempt in range(GROQ_RETRIES):
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": translation_prompt(texts, target)}],
                },
                timeout=180,
            )
            if response.status_code == 429 and attempt < GROQ_RETRIES - 1:
                wait = _retry_after(response, default=20 * (attempt + 1))
                logger.info("groq rate limited, waiting %.0fs", wait)
                await asyncio.sleep(wait)
                continue
            if response.status_code in (401, 403):
                raise SubtitleError("GROQ_API_KEY ditolak Groq, cek lagi key-nya")
            if response.status_code == 429:
                raise SubtitleError("Kuota Groq lagi habis, coba lagi nanti")
            if response.status_code >= 400:
                logger.warning("groq error %s: %s", response.status_code, response.text[:500])
                raise SubtitleError(f"Groq error {response.status_code}")
            choices = response.json().get("choices") or [{}]
            content = choices[0].get("message", {}).get("content") or ""
            return _parse_lines(content, len(texts))
        raise SubtitleError("Kuota Groq lagi habis, coba lagi nanti")


class ClaudeTranslator:
    def __init__(self, client: httpx.AsyncClient, api_key: str, model: str, base_url: str) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._url = f"{base_url.rstrip('/')}/v1/messages"

    async def translate(self, texts: list[str], target: TranslationTarget) -> list[str]:
        if not self._api_key:
            raise SubtitleError("ANTHROPIC_API_KEY belum diisi di backend/.env")
        prompt = translation_prompt(texts, target)
        response = await self._client.post(
            self._url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self._model,
                "max_tokens": 8000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=180,
        )
        if response.status_code in (401, 403):
            raise SubtitleError("ANTHROPIC_API_KEY ditolak, cek lagi key-nya")
        if response.status_code >= 400:
            logger.warning("claude error %s: %s", response.status_code, response.text[:500])
            raise SubtitleError(f"Claude error {response.status_code}")
        body = response.json()
        text = "".join(
            block.get("text", "")
            for block in body.get("content", [])
            if block.get("type") == "text"
        )
        return _parse_lines(text, len(texts))


def build_translator(settings: "Settings", client: httpx.AsyncClient) -> Translator:
    if settings.subtitle_translator == "anthropic":
        return ClaudeTranslator(
            client,
            settings.anthropic_api_key.get_secret_value(),
            settings.claude_translate_model,
            settings.anthropic_base_url,
        )
    return GroqTranslator(
        client,
        settings.groq_api_key.get_secret_value(),
        settings.groq_translate_model,
        settings.groq_base_url,
    )


class BatchMismatch(Exception):
    pass


def _parse_lines(text: str, expected: int) -> list[str]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        raise BatchMismatch("no JSON array in reply")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise BatchMismatch(f"bad JSON: {exc}") from exc
    if not isinstance(parsed, list) or len(parsed) != expected:
        raise BatchMismatch(f"expected {expected} lines, got {len(parsed)}")
    return [str(item).strip() for item in parsed]


async def translate_transcript(
    transcript: Transcript,
    target: TranslationTarget,
    translator: Translator,
    on_progress: ProgressCallback = _ignore_progress,
) -> Transcript:
    if transcript.language == target:
        return Transcript(target, transcript.lines)
    texts = [line.text for line in transcript.lines]
    translated: list[str] = []
    for start in range(0, len(texts), TRANSLATE_BATCH):
        batch = texts[start : start + TRANSLATE_BATCH]
        try:
            translated.extend(await translator.translate(batch, target))
        except BatchMismatch as exc:
            # A refused or malformed batch keeps its original lines: a gap is worse than a
            # line in the spoken language.
            logger.warning("translation batch at line %d kept original: %s", start, exc)
            translated.extend(batch)
        on_progress(min(int(len(translated) / len(texts) * 100), 99))
    lines = [
        Line(line.start, line.end, text or line.text)
        for line, text in zip(transcript.lines, translated, strict=True)
    ]
    return Transcript(target, lines)


# Jobs


async def run_transcription(
    sha256: str,
    video: Path,
    files: SubtitleFiles,
    status: SubtitleStatus,
    transcriber: Transcriber,
    chunk_seconds: int,
) -> None:
    status.set(sha256, "original", "running", progress=0)

    def report(percent: int) -> None:
        status.set(sha256, "original", "running", progress=percent)

    try:
        transcript = await transcribe_video(video, transcriber, chunk_seconds, report)
        files.save(sha256, "original", transcript)
    except SubtitleError as exc:
        status.set(sha256, "original", "failed", str(exc))
        return
    except Exception:
        logger.exception("transcription failed for %s", sha256)
        status.set(sha256, "original", "failed", "Gagal bikin subtitle, cek log worker")
        return
    status.clear(sha256, "original")
    logger.info(
        "subtitle %s: %d lines, %s", sha256[:12], len(transcript.lines), transcript.language
    )


async def run_translation(
    sha256: str,
    target: TranslationTarget,
    files: SubtitleFiles,
    status: SubtitleStatus,
    translator: Translator,
) -> None:
    status.set(sha256, target, "running", progress=0)

    def report(percent: int) -> None:
        status.set(sha256, target, "running", progress=percent)

    try:
        original = files.load(sha256, "original")
        if original is None:
            raise SubtitleError("Subtitle asli belum ada, generate dulu")
        translated = await translate_transcript(original, target, translator, report)
        files.save(sha256, target, translated)
    except SubtitleError as exc:
        status.set(sha256, target, "failed", str(exc))
        return
    except Exception:
        logger.exception("translation to %s failed for %s", target, sha256)
        status.set(sha256, target, "failed", "Gagal nerjemahin subtitle, cek log worker")
        return
    status.clear(sha256, target)
