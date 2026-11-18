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
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

import httpx
from redis import Redis
from redis.exceptions import RedisError

from app.library.ai_quota import BudgetExceeded, ClaudeSpend, QuotaStore

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

TranslationTarget = Literal["en", "id"]
Track = Literal["original", "en", "id"]
TRANSLATION_TARGETS: tuple[TranslationTarget, ...] = ("en", "id")

# Whisper writes text for music and silence too. Lines it rates as likely non speech are dropped.
NO_SPEECH_THRESHOLD = 0.6
# Lines per Claude request: enough context for natural phrasing, small enough to retry cheaply.
TRANSLATE_BATCH = 40
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
    # Who translated it: "groq" or "claude". None for the original and for older files.
    translator: str | None = None
    # Lines changed by hand. A translation made again keeps them as they are.
    edited: frozenset[int] = frozenset()


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
        payload = {
            "language": transcript.language,
            "translator": transcript.translator,
            "edited": sorted(transcript.edited),
            "lines": [asdict(x) for x in transcript.lines],
        }
        # Written beside and renamed, so a reader never sees half a file.
        partial = target.with_suffix(".part")
        partial.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        partial.replace(target)

    def load(self, sha256: str, track: Track) -> Transcript | None:
        path = self.path(sha256, track)
        if not path.exists():
            return None
        raw = json.loads(path.read_text(encoding="utf-8"))
        lines = [Line(**line) for line in raw["lines"]]
        return Transcript(
            raw.get("language"), lines, raw.get("translator"), frozenset(raw.get("edited", []))
        )

    def edit_line(self, sha256: str, track: Track, index: int, text: str) -> Transcript:
        transcript = self.load(sha256, track)
        if transcript is None:
            raise SubtitleError("Subtitle ini belum ada")
        if not 0 <= index < len(transcript.lines):
            raise SubtitleError("Baris subtitle nggak ada")
        lines = list(transcript.lines)
        old = lines[index]
        lines[index] = Line(old.start, old.end, text)
        updated = replace(transcript, lines=lines, edited=transcript.edited | {index})
        self.save(sha256, track, updated)
        return updated

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


# Tried in order when Groq answers 404 for a model: retired, or blocked for the account.
TRANSCRIBE_FALLBACKS = ("whisper-large-v3-turbo",)
TRANSLATE_FALLBACKS = ("openai/gpt-oss-120b", "llama-3.1-8b-instant")
MAX_TRANSLATION_TOKENS = 4096


def _groq_message(response: httpx.Response) -> str:
    # Groq and Anthropic both answer errors as {"error": {"message": ...}}.
    try:
        message = response.json().get("error", {}).get("message", "")
    except ValueError:
        message = response.text
    return str(message)[:400]


async def _call_groq(
    send: Callable[[str], Awaitable[httpx.Response]],
    models: list[str],
    step: str,
    record: Callable[[str, httpx.Response], None] | None = None,
    batch_errors: tuple[int, ...] = (),
) -> httpx.Response:
    """Send with each model in turn until one exists, retrying rate limits along the way.

    Statuses in batch_errors are a problem with this batch, not with the job: they raise
    BatchMismatch, so the lines are asked for again in smaller groups.
    """
    last_error = ""
    for model in dict.fromkeys(models):
        for attempt in range(GROQ_RETRIES):
            response = await send(model)
            if record is not None:
                record(model, response)
            if response.status_code == 429 and attempt < GROQ_RETRIES - 1:
                wait = _retry_after(response, default=20 * (attempt + 1))
                logger.info("groq rate limited, waiting %.0fs", wait)
                await asyncio.sleep(wait)
                continue
            break
        if response.status_code == 404:
            last_error = _groq_message(response)
            logger.warning("groq %s: model %s not available: %s", step, model, last_error)
            continue
        if response.status_code in (401, 403):
            raise SubtitleError("GROQ_API_KEY ditolak Groq, cek lagi key-nya")
        if response.status_code == 429:
            raise SubtitleError("Kuota Groq lagi habis, coba lagi nanti")
        if response.status_code in batch_errors:
            message = _groq_message(response)
            logger.warning("groq %s batch refused %s: %s", step, response.status_code, message)
            raise BatchMismatch(f"groq {response.status_code}: {message}")
        if response.status_code >= 400:
            message = _groq_message(response)
            logger.warning("groq %s error %s: %s", step, response.status_code, message)
            raise SubtitleError(f"Groq {step} error {response.status_code}: {message}")
        if model != models[0]:
            logger.info("groq %s used fallback model %s", step, model)
        return response
    raise SubtitleError(f"Model Groq buat {step} nggak tersedia: {last_error}")


class GroqTranscriber:
    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        model: str,
        base_url: str,
        quota: QuotaStore | None = None,
    ) -> None:
        self._quota = quota
        self._client = client
        self._api_key = api_key
        self._models = [model, *TRANSCRIBE_FALLBACKS]
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"

    def _record(self, model: str, response: httpx.Response) -> None:
        if self._quota is not None:
            self._quota.record("groq", "transcribe", model, response)

    async def transcribe(self, audio: Path) -> tuple[str | None, list[Line]]:
        if not self._api_key:
            raise SubtitleError("GROQ_API_KEY belum diisi di backend/.env")

        async def send(model: str) -> httpx.Response:
            with audio.open("rb") as handle:
                return await self._client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    data={
                        "model": model,
                        "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment",
                        "temperature": "0",
                    },
                    files={"file": (audio.name, handle, "audio/ogg")},
                    timeout=300,
                )

        response = await _call_groq(send, self._models, "transkrip", self._record)
        body = response.json()
        return normalize_language(body.get("language")), _speech_lines(body.get("segments"))


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
    name: str

    async def translate(self, texts: list[str], target: TranslationTarget) -> dict[int, str]:
        """Translated lines by their position in texts. Lines it could not do are left out."""
        ...


def translation_prompt(texts: list[str], target: TranslationTarget) -> str:
    # Numbered lines instead of a plain list: a reply that merges or skips a line still lines up,
    # and only the missing numbers are asked for again.
    numbered = {str(index + 1): text for index, text in enumerate(texts)}
    return (
        f"Translate these numbered video subtitle lines into {TARGET_NAMES[target]}. "
        "Translate every line, keep each one short like a subtitle and keep names as they are. "
        "Reply with only a JSON object with the same numbers as keys and the translated lines "
        "as values.\n\n" + json.dumps(numbered, ensure_ascii=False)
    )


class GroqTranslator:
    """A text model on Groq, the same key and free tier as the transcription."""

    name = "groq"

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        model: str,
        base_url: str,
        quota: QuotaStore | None = None,
    ) -> None:
        self._quota = quota
        self._client = client
        self._api_key = api_key
        self._models = [model, *TRANSLATE_FALLBACKS]
        self._url = f"{base_url.rstrip('/')}/chat/completions"

    def _record(self, model: str, response: httpx.Response) -> None:
        if self._quota is not None:
            self._quota.record("groq", "translate", model, response)

    async def translate(self, texts: list[str], target: TranslationTarget) -> dict[int, str]:
        if not self._api_key:
            raise SubtitleError("GROQ_API_KEY belum diisi di backend/.env")

        async def send(model: str) -> httpx.Response:
            return await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": model,
                    "temperature": 0.2,
                    "response_format": {"type": "json_object"},
                    # Groq counts the reply allowance against tokens per minute up front: a modest
                    # cap keeps one request from asking for more than the whole minute's budget.
                    "max_completion_tokens": MAX_TRANSLATION_TOKENS,
                    **({"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else {}),
                    "messages": [{"role": "user", "content": translation_prompt(texts, target)}],
                },
                timeout=180,
            )

        # 400: the model wrote broken JSON (json_validate_failed). 413: the batch is too big
        # for the tokens per minute limit. Both get better with fewer lines.
        response = await _call_groq(
            send, self._models, "terjemahan", self._record, batch_errors=(400, 413)
        )
        choices = response.json().get("choices") or [{}]
        content = choices[0].get("message", {}).get("content") or ""
        return _parse_numbered(content, len(texts))


class ClaudeTranslator:
    name = "claude"

    def __init__(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        model: str,
        base_url: str,
        quota: QuotaStore | None = None,
        spend: ClaudeSpend | None = None,
        workspace_id: str = "",
    ) -> None:
        self._quota = quota
        self._spend = spend
        self._workspace_id = workspace_id
        self._client = client
        self._api_key = api_key
        self._model = model
        self._url = f"{base_url.rstrip('/')}/v1/messages"

    def _record(self, response: httpx.Response) -> None:
        if self._quota is not None:
            self._quota.record("anthropic", "translate", self._model, response)

    async def translate(self, texts: list[str], target: TranslationTarget) -> dict[int, str]:
        if not self._api_key:
            raise SubtitleError("ANTHROPIC_API_KEY belum diisi di backend/.env")
        if self._spend is not None:
            try:
                self._spend.check()
            except BudgetExceeded as exc:
                raise SubtitleError(str(exc)) from exc
        prompt = translation_prompt(texts, target)
        response = await self._client.post(
            self._url,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
                # Needed only for a key that is not tied to one workspace.
                **({"anthropic-workspace-id": self._workspace_id} if self._workspace_id else {}),
            },
            json={
                "model": self._model,
                "max_tokens": 8000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=180,
        )
        self._record(response)
        if response.status_code in (401, 403):
            raise SubtitleError("ANTHROPIC_API_KEY ditolak, cek lagi key-nya")
        if response.status_code >= 400:
            message = _groq_message(response)
            logger.warning("claude error %s: %s", response.status_code, message)
            if "credit balance" in message.lower():
                raise SubtitleError("Saldo kredit Anthropic habis, top up di console")
            if "workspace" in message.lower() and not self._workspace_id:
                raise SubtitleError(
                    "Key Anthropic ini belum terikat ke workspace. Bikin key di dalam workspace "
                    "di console, atau isi ANTHROPIC_WORKSPACE_ID di backend/.env"
                )
            raise SubtitleError(f"Claude error {response.status_code}: {message}")
        body = response.json()
        usage = body.get("usage") or {}
        if self._spend is not None:
            self._spend.record(
                int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            )
        text = "".join(
            block.get("text", "")
            for block in body.get("content", [])
            if block.get("type") == "text"
        )
        return _parse_numbered(text, len(texts))


def build_claude_spend(settings: "Settings", redis: Redis) -> ClaudeSpend:
    return ClaudeSpend(
        redis,
        settings.claude_input_usd_per_mtok,
        settings.claude_output_usd_per_mtok,
        settings.claude_monthly_budget_usd,
    )


def build_claude(
    settings: "Settings",
    client: httpx.AsyncClient,
    quota: QuotaStore | None = None,
    spend: ClaudeSpend | None = None,
) -> "ClaudeTranslator":
    return ClaudeTranslator(
        client,
        settings.anthropic_api_key.get_secret_value(),
        settings.claude_translate_model,
        settings.anthropic_base_url,
        quota,
        spend,
        settings.anthropic_workspace_id,
    )


def build_translator(
    settings: "Settings",
    client: httpx.AsyncClient,
    quota: QuotaStore | None = None,
    spend: ClaudeSpend | None = None,
) -> Translator:
    if settings.subtitle_translator == "anthropic":
        return build_claude(settings, client, quota, spend)
    return GroqTranslator(
        client,
        settings.groq_api_key.get_secret_value(),
        settings.groq_translate_model,
        settings.groq_base_url,
        quota,
    )


class BatchMismatch(Exception):
    pass


def _parse_numbered(text: str, count: int) -> dict[int, str]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise BatchMismatch(f"no JSON object in reply: {text[:120]!r}")
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise BatchMismatch(f"bad JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BatchMismatch("reply is not an object")
    lines: dict[int, str] = {}
    for key, value in parsed.items():
        if str(key).isdigit() and 1 <= int(key) <= count and str(value).strip():
            lines[int(key) - 1] = str(value).strip()
    return lines


# A batch that comes back short is asked again in halves, down to single lines for a batch
# of TRANSLATE_BATCH. A refused line then costs about two requests per halving.
MAX_SPLITS = 6


async def _translate_lines(
    texts: list[str],
    indices: list[int],
    target: TranslationTarget,
    translator: Translator,
    results: dict[int, str],
    depth: int = 0,
) -> None:
    try:
        got = await translator.translate([texts[i] for i in indices], target)
    except BatchMismatch as exc:
        logger.warning("translation of %d lines failed: %s", len(indices), exc)
        got = {}
    for position, text in got.items():
        results[indices[position]] = text
    missing = [index for position, index in enumerate(indices) if position not in got]
    if not missing or len(indices) == 1 or depth >= MAX_SPLITS:
        return
    half = max(len(missing) // 2, 1)
    for part in (missing[:half], missing[half:]):
        if part:
            await _translate_lines(texts, part, target, translator, results, depth + 1)


async def translate_transcript(
    transcript: Transcript,
    target: TranslationTarget,
    translator: Translator,
    on_progress: ProgressCallback = _ignore_progress,
    keep: dict[int, str] | None = None,
) -> Transcript:
    """Translate every line except those in keep (edited by hand), which stay as given."""
    keep = keep or {}
    if transcript.language == target:
        return Transcript(target, transcript.lines)
    texts = [line.text for line in transcript.lines]
    results: dict[int, str] = dict(keep)
    todo = [index for index in range(len(texts)) if index not in keep]
    for start in range(0, len(todo), TRANSLATE_BATCH):
        batch = todo[start : start + TRANSLATE_BATCH]
        await _translate_lines(texts, batch, target, translator, results)
        on_progress(min(int((start + len(batch)) / max(len(todo), 1) * 100), 99))
    missing = len(texts) - len(results)
    if missing:
        # Refused or dropped even line by line: those keep the spoken language, a gap is worse.
        logger.warning(
            "translation to %s kept %d of %d lines original", target, missing, len(texts)
        )
    lines = [
        Line(line.start, line.end, results.get(index, line.text))
        for index, line in enumerate(transcript.lines)
    ]
    return Transcript(target, lines, translator.name, frozenset(keep))


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


def _edited_lines(files: SubtitleFiles, sha256: str, track: Track) -> dict[int, str]:
    saved = files.load(sha256, track)
    if saved is None:
        return {}
    return {i: saved.lines[i].text for i in saved.edited if i < len(saved.lines)}


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
        # A translation made again replaces the old file only once it is done, and keeps the
        # lines edited by hand, read again at the end so edits made meanwhile are not lost.
        translated = await translate_transcript(
            original, target, translator, report, keep=_edited_lines(files, sha256, target)
        )
        latest = _edited_lines(files, sha256, target)
        if latest:
            lines = [
                Line(line.start, line.end, latest.get(index, line.text))
                for index, line in enumerate(translated.lines)
            ]
            translated = replace(translated, lines=lines, edited=frozenset(latest))
        files.save(sha256, target, translated)
    except SubtitleError as exc:
        status.set(sha256, target, "failed", str(exc))
        return
    except Exception:
        logger.exception("translation to %s failed for %s", target, sha256)
        status.set(sha256, target, "failed", "Gagal nerjemahin subtitle, cek log worker")
        return
    status.clear(sha256, target)
