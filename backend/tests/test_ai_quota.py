from pathlib import Path

import fakeredis
import httpx
import respx

from app.library.ai_quota import ClaudeSpend, QuotaStore, parse_limits
from app.library.subtitles import ClaudeTranslator, GroqTranscriber

GROQ_HEADERS = {
    "x-ratelimit-limit-requests": "2000",
    "x-ratelimit-remaining-requests": "1987",
    "x-ratelimit-reset-requests": "6m14s",
    "x-ratelimit-limit-audio-seconds": "28800",
    "x-ratelimit-remaining-audio-seconds": "26400",
    "content-type": "application/json",
}
ANTHROPIC_HEADERS = {
    "anthropic-ratelimit-requests-limit": "50",
    "anthropic-ratelimit-requests-remaining": "49",
    "anthropic-ratelimit-requests-reset": "2026-11-18T12:00:00Z",
    "anthropic-ratelimit-output-tokens-remaining": "7900",
}


def test_groq_and_anthropic_headers_group_per_resource() -> None:
    # Arrange
    groq = httpx.Headers(GROQ_HEADERS)
    anthropic = httpx.Headers(ANTHROPIC_HEADERS)

    # Act
    groq_limits = parse_limits("groq", groq)
    anthropic_limits = parse_limits("anthropic", anthropic)

    # Assert
    assert groq_limits == [
        {"resource": "audio-seconds", "limit": "28800", "remaining": "26400"},
        {"resource": "requests", "limit": "2000", "remaining": "1987", "reset": "6m14s"},
    ]
    assert anthropic_limits == [
        {"resource": "output-tokens", "remaining": "7900"},
        {
            "resource": "requests",
            "limit": "50",
            "remaining": "49",
            "reset": "2026-11-18T12:00:00Z",
        },
    ]


@respx.mock
async def test_calls_leave_their_limits_in_the_store(tmp_path: Path) -> None:
    # Arrange
    store = QuotaStore(fakeredis.FakeRedis())
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")
    respx.post("https://groq.test/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, headers=GROQ_HEADERS, json={"segments": []})
    )
    respx.post("https://claude.test/v1/messages").mock(
        return_value=httpx.Response(
            200, headers=ANTHROPIC_HEADERS, json={"content": [{"type": "text", "text": "{}"}]}
        )
    )
    async with httpx.AsyncClient() as client:
        groq = GroqTranscriber(client, "gsk", "whisper-large-v3", "https://groq.test/v1", store)
        claude = ClaudeTranslator(client, "sk", "claude-haiku-5-5", "https://claude.test", store)

        # Act
        await groq.transcribe(audio)
        await claude.translate(["hi"], "id")

    # Assert
    entries = store.all()
    assert [(e["provider"], e["scope"], e["model"]) for e in entries] == [
        ("groq", "transcribe", "whisper-large-v3"),
        ("anthropic", "translate", "claude-haiku-5-5"),
    ]
    assert entries[0]["limits"][1]["remaining"] == "1987"
    assert entries[0]["updated_at"]


def test_response_without_limit_headers_keeps_the_previous_numbers() -> None:
    # Arrange
    store = QuotaStore(fakeredis.FakeRedis())
    store.record("groq", "translate", "m", httpx.Response(200, headers=GROQ_HEADERS))

    # Act
    store.record("groq", "translate", "m", httpx.Response(500))

    # Assert
    assert store.all()[0]["limits"][1]["remaining"] == "1987"


@respx.mock
async def test_empty_anthropic_balance_says_so() -> None:
    # Arrange
    import pytest

    from app.library.subtitles import SubtitleError

    body = {
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "Your credit balance is too low."},
    }
    respx.post("https://claude.test/v1/messages").mock(return_value=httpx.Response(400, json=body))
    async with httpx.AsyncClient() as client:
        claude = ClaudeTranslator(client, "sk", "claude-haiku-5-5", "https://claude.test")

        # Act / Assert
        with pytest.raises(SubtitleError, match="Saldo kredit Anthropic habis"):
            await claude.translate(["hi"], "id")


def test_claude_spend_is_priced_per_million_tokens_and_checked_against_the_budget() -> None:
    # Arrange
    spend = ClaudeSpend(fakeredis.FakeRedis(), 0.10, 0.50, 0.002)

    # Act
    spend.record(10_000, 1_000)
    under = spend.is_over_budget()
    spend.record(5_000, 1_000)
    over = spend.is_over_budget()

    # Assert
    month = spend.month()
    assert (month["input_tokens"], month["output_tokens"]) == (15_000, 2_000)
    assert month["usd"] == 0.0025
    assert (under, over) == (False, True)


@respx.mock
async def test_claude_records_its_usage_and_stops_at_the_budget() -> None:
    # Arrange
    import pytest

    from app.library.subtitles import SubtitleError

    spend = ClaudeSpend(fakeredis.FakeRedis(), 0.10, 0.50, 0.0001)
    reply = {
        "content": [{"type": "text", "text": '{"1": "halo"}'}],
        "usage": {"input_tokens": 400, "output_tokens": 100},
    }
    route = respx.post("https://claude.test/v1/messages").mock(
        return_value=httpx.Response(200, json=reply)
    )
    async with httpx.AsyncClient() as client:
        claude = ClaudeTranslator(
            client, "sk", "claude-haiku-5-5", "https://claude.test", None, spend
        )

        # Act
        first = await claude.translate(["hi"], "id")
        with pytest.raises(SubtitleError, match="Budget Claude"):
            await claude.translate(["hi"], "id")

    # Assert
    assert first == {0: "halo"}
    assert route.call_count == 1
    assert spend.month()["input_tokens"] == 400


@respx.mock
async def test_workspace_id_is_sent_when_set_and_its_absence_is_explained() -> None:
    # Arrange
    import pytest

    from app.library.subtitles import SubtitleError

    unscoped = {
        "error": {
            "message": "This API key is not scoped to a workspace, so this request must "
            "include the anthropic-workspace-id header."
        }
    }
    ok = {"content": [{"type": "text", "text": '{"1": "halo"}'}]}
    route = respx.post("https://claude.test/v1/messages").mock(
        side_effect=[httpx.Response(400, json=unscoped), httpx.Response(200, json=ok)]
    )
    async with httpx.AsyncClient() as client:
        plain = ClaudeTranslator(client, "sk", "claude-haiku-5-5", "https://claude.test")
        scoped = ClaudeTranslator(
            client, "sk", "claude-haiku-5-5", "https://claude.test", workspace_id="wrkspc_1"
        )

        # Act
        with pytest.raises(SubtitleError, match="ANTHROPIC_WORKSPACE_ID"):
            await plain.translate(["hi"], "id")
        lines = await scoped.translate(["hi"], "id")

    # Assert
    assert lines == {0: "halo"}
    assert "anthropic-workspace-id" not in route.calls[0].request.headers
    assert route.calls[1].request.headers["anthropic-workspace-id"] == "wrkspc_1"
