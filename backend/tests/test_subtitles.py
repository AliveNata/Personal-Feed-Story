import json
import subprocess
from pathlib import Path

import fakeredis
import httpx
import pytest
import respx

from app.library import subtitles
from app.library.subtitles import (
    BatchMismatch,
    ClaudeTranslator,
    GroqTranscriber,
    GroqTranslator,
    Line,
    SubtitleError,
    SubtitleFiles,
    SubtitleStatus,
    Transcript,
    TranslationTarget,
)

GROQ_URL = "https://groq.test/openai/v1/audio/transcriptions"
CLAUDE_URL = "https://claude.test/v1/messages"
GROQ_CHAT_URL = "https://groq.test/openai/v1/chat/completions"


def make_video(path: Path, seconds: int, with_audio: bool = True) -> Path:
    args = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s=64x64:d={seconds}",
    ]
    if with_audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest"]
    subprocess.run([*args, "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


class FakeTranscriber:
    def __init__(self, language: str | None = "en") -> None:
        self.language = language
        self.calls: list[Path] = []

    async def transcribe(self, audio: Path) -> tuple[str | None, list[Line]]:
        self.calls.append(audio)
        return self.language, [Line(0.5, 1.5, f"halo {len(self.calls)}")]


class FakeTranslator:
    def __init__(self, broken_batches: set[int] | None = None) -> None:
        self.batches = 0
        self.broken = broken_batches or set()

    async def translate(self, texts: list[str], target: TranslationTarget) -> list[str]:
        self.batches += 1
        if self.batches in self.broken:
            raise BatchMismatch("refused")
        return [f"[{target}] {text}" for text in texts]


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(subtitles.asyncio, "sleep", instant)


def test_language_names_and_codes_normalize_to_iso_codes() -> None:
    # Arrange
    raw = ["English", "indonesian", "id", "Klingon", None]

    # Act
    codes = [subtitles.normalize_language(value) for value in raw]

    # Assert
    assert codes == ["en", "id", "id", None, None]


async def test_audio_is_cut_into_pieces_with_their_offsets(tmp_path: Path) -> None:
    # Arrange
    video = make_video(tmp_path / "clip.mp4", seconds=5)
    work = tmp_path / "work"
    work.mkdir()

    # Act
    chunks = await subtitles.extract_audio_chunks(video, work, chunk_seconds=2)

    # Assert
    assert [offset for offset, _ in chunks] == [0.0, 2.0, 4.0]
    assert all(path.stat().st_size > 0 for _, path in chunks)


async def test_video_without_sound_says_so(tmp_path: Path) -> None:
    # Arrange
    video = make_video(tmp_path / "silent.mp4", seconds=2, with_audio=False)

    # Act / Assert
    with pytest.raises(SubtitleError, match="nggak ada suaranya"):
        await subtitles.extract_audio_chunks(video, tmp_path, chunk_seconds=60)


async def test_transcript_lines_are_shifted_by_their_piece_offset(tmp_path: Path) -> None:
    # Arrange
    video = make_video(tmp_path / "clip.mp4", seconds=5)
    transcriber = FakeTranscriber(language="id")

    # Act
    transcript = await subtitles.transcribe_video(video, transcriber, chunk_seconds=2)

    # Assert
    assert transcript.language == "id"
    assert [(line.start, line.text) for line in transcript.lines] == [
        (0.5, "halo 1"),
        (2.5, "halo 2"),
        (4.5, "halo 3"),
    ]


async def test_progress_climbs_per_piece_and_stops_short_of_done(tmp_path: Path) -> None:
    # Arrange
    video = make_video(tmp_path / "clip.mp4", seconds=4)
    seen: list[int] = []

    # Act
    await subtitles.transcribe_video(video, FakeTranscriber(), 2, seen.append)

    # Assert
    assert seen == [15, 50, 65, 99]


async def test_translation_progress_follows_the_batches() -> None:
    # Arrange
    count = subtitles.TRANSLATE_BATCH * 2
    original = Transcript("en", [Line(i, i + 1, f"line {i}") for i in range(count)])
    seen: list[int] = []

    # Act
    await subtitles.translate_transcript(original, "id", FakeTranslator(), seen.append)

    # Assert
    assert seen == [50, 99]


@respx.mock
async def test_groq_drops_non_speech_and_retries_after_rate_limit(tmp_path: Path) -> None:
    # Arrange
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"OggS fake")
    body = {
        "language": "English",
        "segments": [
            {"start": 0.0, "end": 2.0, "text": " Hello there ", "no_speech_prob": 0.01},
            {"start": 2.0, "end": 4.0, "text": " ♪ ", "no_speech_prob": 0.9},
            {"start": 4.0, "end": 5.0, "text": "  ", "no_speech_prob": 0.0},
        ],
    }
    route = respx.post(GROQ_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"retry-after": "1"}),
            httpx.Response(200, json=body),
        ]
    )
    async with httpx.AsyncClient() as client:
        groq = GroqTranscriber(
            client, "gsk_test", "whisper-large-v3", "https://groq.test/openai/v1"
        )

        # Act
        language, lines = await groq.transcribe(audio)

    # Assert
    assert route.call_count == 2
    assert language == "en"
    assert lines == [Line(0.0, 2.0, "Hello there")]


@respx.mock
async def test_groq_rejected_key_is_a_readable_error(tmp_path: Path) -> None:
    # Arrange
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")
    respx.post(GROQ_URL).mock(return_value=httpx.Response(401))
    async with httpx.AsyncClient() as client:
        groq = GroqTranscriber(client, "bad", "whisper-large-v3", "https://groq.test/openai/v1")

        # Act / Assert
        with pytest.raises(SubtitleError, match="GROQ_API_KEY"):
            await groq.transcribe(audio)


@respx.mock
async def test_claude_reply_is_read_even_with_text_around_the_array() -> None:
    # Arrange
    reply = {"content": [{"type": "text", "text": 'Here you go:\n["Halo", "Apa kabar?"]'}]}
    route = respx.post(CLAUDE_URL).mock(return_value=httpx.Response(200, json=reply))
    async with httpx.AsyncClient() as client:
        claude = ClaudeTranslator(client, "sk-ant-test", "claude-haiku-5-5", "https://claude.test")

        # Act
        lines = await claude.translate(["Hello", "How are you?"], "id")

    # Assert
    assert lines == ["Halo", "Apa kabar?"]
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "claude-haiku-5-5"
    assert route.calls[0].request.headers["x-api-key"] == "sk-ant-test"


@respx.mock
async def test_claude_reply_with_wrong_line_count_is_a_batch_mismatch() -> None:
    # Arrange
    reply = {"content": [{"type": "text", "text": '["only one"]'}]}
    respx.post(CLAUDE_URL).mock(return_value=httpx.Response(200, json=reply))
    async with httpx.AsyncClient() as client:
        claude = ClaudeTranslator(client, "sk-ant-test", "claude-haiku-5-5", "https://claude.test")

        # Act / Assert
        with pytest.raises(BatchMismatch):
            await claude.translate(["a", "b"], "en")


@respx.mock
async def test_groq_translator_reads_the_chat_reply_and_retries_after_rate_limit() -> None:
    # Arrange
    reply = {"choices": [{"message": {"content": '```json\n["Halo", "Sampai jumpa"]\n```'}}]}
    route = respx.post(GROQ_CHAT_URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=reply)]
    )
    async with httpx.AsyncClient() as client:
        groq = GroqTranslator(
            client, "gsk_test", "llama-3.3-70b-versatile", "https://groq.test/openai/v1"
        )

        # Act
        lines = await groq.translate(["Hello", "See you"], "id")

    # Assert
    assert lines == ["Halo", "Sampai jumpa"]
    assert route.call_count == 2
    sent = json.loads(route.calls[1].request.content)
    assert sent["model"] == "llama-3.3-70b-versatile"
    assert route.calls[1].request.headers["authorization"] == "Bearer gsk_test"


def test_translator_follows_the_setting() -> None:
    # Arrange
    from app.config import Settings

    groq_settings = Settings(_env_file=None, subtitle_translator="groq")
    claude_settings = Settings(_env_file=None, subtitle_translator="anthropic")
    client = httpx.AsyncClient()

    # Act
    default = subtitles.build_translator(groq_settings, client)
    claude = subtitles.build_translator(claude_settings, client)

    # Assert
    assert isinstance(default, GroqTranslator)
    assert isinstance(claude, ClaudeTranslator)
    assert Settings(_env_file=None).subtitle_translator == "groq"


async def test_refused_batch_keeps_original_lines_and_others_are_translated() -> None:
    # Arrange
    count = subtitles.TRANSLATE_BATCH + 5
    original = Transcript("en", [Line(i, i + 1, f"line {i}") for i in range(count)])
    translator = FakeTranslator(broken_batches={2})

    # Act
    result = await subtitles.translate_transcript(original, "id", translator)

    # Assert
    assert result.language == "id"
    assert result.lines[0].text == "[id] line 0"
    assert result.lines[-1].text == f"line {count - 1}"
    assert [line.start for line in result.lines] == [line.start for line in original.lines]


async def test_same_language_target_is_not_sent_for_translation() -> None:
    # Arrange
    original = Transcript("id", [Line(0, 1, "halo")])
    translator = FakeTranslator()

    # Act
    result = await subtitles.translate_transcript(original, "id", translator)

    # Assert
    assert translator.batches == 0
    assert result.lines == original.lines


async def test_transcription_job_saves_the_track_and_clears_its_state(tmp_path: Path) -> None:
    # Arrange
    video = make_video(tmp_path / "clip.mp4", seconds=2)
    files = SubtitleFiles(tmp_path / "library")
    status = SubtitleStatus(fakeredis.FakeRedis())
    status.set("abc", "original", "queued")

    # Act
    await subtitles.run_transcription("abc", video, files, status, FakeTranscriber(), 60)

    # Assert
    saved = files.load("abc", "original")
    assert saved is not None and saved.language == "en"
    assert status.get("abc", "original") is None


async def test_failed_job_keeps_a_readable_error(tmp_path: Path) -> None:
    # Arrange
    video = make_video(tmp_path / "silent.mp4", seconds=2, with_audio=False)
    files = SubtitleFiles(tmp_path / "library")
    status = SubtitleStatus(fakeredis.FakeRedis())

    # Act
    await subtitles.run_transcription("abc", video, files, status, FakeTranscriber(), 60)

    # Assert
    assert status.get("abc", "original") == {
        "state": "failed",
        "error": "Video ini nggak ada suaranya",
        "progress": None,
    }
    assert not files.has("abc", "original")


async def test_translation_job_needs_the_original_first(tmp_path: Path) -> None:
    # Arrange
    files = SubtitleFiles(tmp_path / "library")
    status = SubtitleStatus(fakeredis.FakeRedis())

    files.save("abc", "original", Transcript("id", [Line(0, 1, "halo")]))

    # Act
    await subtitles.run_translation("def", "en", files, status, FakeTranslator())
    await subtitles.run_translation("abc", "en", files, status, FakeTranslator())

    # Assert
    first = status.get("def", "en")
    assert first is not None and first["state"] == "failed"
    translated = files.load("abc", "en")
    assert translated is not None and translated.lines[0].text == "[en] halo"
