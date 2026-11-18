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
    """Refuses any batch holding a refused line, and drops the last line of big batches."""

    name = "fake"

    def __init__(self, refused: set[str] | None = None, drops_over: int | None = None) -> None:
        self.batches = 0
        self.sizes: list[int] = []
        self.refused = refused or set()
        self.drops_over = drops_over

    async def translate(self, texts: list[str], target: TranslationTarget) -> dict[int, str]:
        self.batches += 1
        self.sizes.append(len(texts))
        if self.refused & set(texts):
            raise BatchMismatch("refused")
        lines = {index: f"[{target}] {text}" for index, text in enumerate(texts)}
        if self.drops_over is not None and len(texts) > self.drops_over:
            lines.pop(len(texts) - 1)
        return lines


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
async def test_retired_groq_model_falls_back_and_names_the_step_when_none_work(
    tmp_path: Path,
) -> None:
    # Arrange
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")
    gone = httpx.Response(404, json={"error": {"message": "model does not exist"}})
    ok = httpx.Response(200, json={"language": "id", "segments": []})
    route = respx.post(GROQ_URL).mock(side_effect=[gone, ok])
    chat = respx.post(GROQ_CHAT_URL).mock(return_value=gone)
    async with httpx.AsyncClient() as client:
        groq = GroqTranscriber(client, "gsk", "whisper-old", "https://groq.test/openai/v1")
        translator = GroqTranslator(client, "gsk", "llama-old", "https://groq.test/openai/v1")

        # Act
        language, _ = await groq.transcribe(audio)
        with pytest.raises(SubtitleError) as failure:
            await translator.translate(["hi"], "id")

    # Assert
    assert language == "id"
    models = [json_field(call.request, "model") for call in route.calls]
    assert models == ["whisper-old", "whisper-large-v3-turbo"]
    assert chat.call_count == 3
    assert "terjemahan" in str(failure.value) and "model does not exist" in str(failure.value)


def json_field(request: httpx.Request, name: str) -> str:
    body = request.content.decode(errors="ignore")
    if request.headers.get("content-type", "").startswith("application/json"):
        return str(json.loads(body)[name])
    marker = f'name="{name}"'
    return body.split(marker, 1)[1].split("\r\n\r\n", 1)[1].split("\r\n", 1)[0]


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
    reply = {
        "content": [{"type": "text", "text": 'Here you go:\n{"1": "Halo", "2": "Apa kabar?"}'}]
    }
    route = respx.post(CLAUDE_URL).mock(return_value=httpx.Response(200, json=reply))
    async with httpx.AsyncClient() as client:
        claude = ClaudeTranslator(client, "sk-ant-test", "claude-haiku-5-5", "https://claude.test")

        # Act
        lines = await claude.translate(["Hello", "How are you?"], "id")

    # Assert
    assert lines == {0: "Halo", 1: "Apa kabar?"}
    sent = json.loads(route.calls[0].request.content)
    assert '"2": "How are you?"' in sent["messages"][0]["content"]
    assert sent["model"] == "claude-haiku-5-5"
    assert route.calls[0].request.headers["x-api-key"] == "sk-ant-test"


@respx.mock
async def test_reply_without_json_is_a_batch_mismatch_and_short_reply_is_partial() -> None:
    # Arrange
    refusal = {"content": [{"type": "text", "text": "I can't help with that."}]}
    short = {"content": [{"type": "text", "text": '{"2": "dua", "9": "out of range"}'}]}
    respx.post(CLAUDE_URL).mock(
        side_effect=[httpx.Response(200, json=refusal), httpx.Response(200, json=short)]
    )
    async with httpx.AsyncClient() as client:
        claude = ClaudeTranslator(client, "sk-ant-test", "claude-haiku-5-5", "https://claude.test")

        # Act
        with pytest.raises(BatchMismatch):
            await claude.translate(["a", "b"], "en")
        partial = await claude.translate(["a", "b"], "en")

    # Assert
    assert partial == {1: "dua"}


@respx.mock
async def test_groq_translator_reads_the_chat_reply_and_retries_after_rate_limit() -> None:
    # Arrange
    reply = {"choices": [{"message": {"content": '{"1": "Halo", "2": "Sampai jumpa"}'}}]}
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
    assert lines == {0: "Halo", 1: "Sampai jumpa"}
    assert route.call_count == 2
    sent = json.loads(route.calls[1].request.content)
    assert sent["response_format"] == {"type": "json_object"}
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


async def test_refused_batch_is_split_until_only_the_refused_line_stays_original() -> None:
    # Arrange
    count = subtitles.TRANSLATE_BATCH + 5
    original = Transcript("en", [Line(i, i + 1, f"line {i}") for i in range(count)])
    translator = FakeTranslator(refused={"line 3"})

    # Act
    result = await subtitles.translate_transcript(original, "id", translator)

    # Assert
    texts = [line.text for line in result.lines]
    assert texts[3] == "line 3"
    assert [t for t in texts if not t.startswith("[id]")] == ["line 3"]
    assert [line.start for line in result.lines] == [line.start for line in original.lines]


async def test_lines_a_reply_skipped_are_asked_for_again() -> None:
    # Arrange
    original = Transcript("en", [Line(i, i + 1, f"line {i}") for i in range(10)])
    translator = FakeTranslator(drops_over=5)

    # Act
    result = await subtitles.translate_transcript(original, "id", translator)

    # Assert
    assert all(line.text.startswith("[id]") for line in result.lines)
    assert translator.sizes == [10, 1]


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
    assert translated.translator == "fake"


async def test_redo_keeps_lines_edited_by_hand(tmp_path: Path) -> None:
    # Arrange
    files = SubtitleFiles(tmp_path / "library")
    status = SubtitleStatus(fakeredis.FakeRedis())
    files.save("abc", "original", Transcript("en", [Line(0, 1, "hello"), Line(1, 2, "bye")]))
    files.save("abc", "id", Transcript("id", [Line(0, 1, "hello"), Line(1, 2, "dadah")], "groq"))
    files.edit_line("abc", "id", 0, "halo bos")
    translator = FakeTranslator()

    # Act
    await subtitles.run_translation("abc", "id", files, status, translator)

    # Assert
    redone = files.load("abc", "id")
    assert redone is not None
    assert [line.text for line in redone.lines] == ["halo bos", "[id] bye"]
    assert redone.edited == frozenset({0})
    assert translator.sizes == [1]


def test_editing_a_missing_line_is_an_error(tmp_path: Path) -> None:
    # Arrange
    files = SubtitleFiles(tmp_path / "library")
    files.save("abc", "original", Transcript("en", [Line(0, 1, "hello")]))

    # Act / Assert
    with pytest.raises(SubtitleError):
        files.edit_line("abc", "original", 5, "x")
    with pytest.raises(SubtitleError):
        files.edit_line("abc", "en", 0, "x")


@respx.mock
async def test_groq_broken_json_or_oversized_batch_is_retried_in_smaller_groups() -> None:
    # Arrange
    calls: list[int] = []

    def reply(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        prompt = body["messages"][0]["content"]
        numbered = json.loads(prompt[prompt.index("\n\n{") + 2 :])
        calls.append(len(numbered))
        if len(numbered) > 2:
            status = 400 if len(calls) == 1 else 413
            return httpx.Response(status, json={"error": {"message": "json_validate_failed"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({k: f"ok {v}" for k, v in numbered.items()})
                        }
                    }
                ]
            },
        )

    respx.post(GROQ_CHAT_URL).mock(side_effect=reply)
    original = Transcript("en", [Line(i, i + 1, f"l{i}") for i in range(4)])
    async with httpx.AsyncClient() as client:
        groq = GroqTranslator(client, "gsk", "openai/gpt-oss-20b", "https://groq.test/openai/v1")

        # Act
        result = await subtitles.translate_transcript(original, "id", groq)

    # Assert
    assert [line.text for line in result.lines] == ["ok l0", "ok l1", "ok l2", "ok l3"]
    assert calls == [4, 2, 2]
    sent = json.loads(respx.calls[0].request.content)
    assert sent["reasoning_effort"] == "low"
    assert sent["max_completion_tokens"] == subtitles.MAX_TRANSLATION_TOKENS
