import json
from typing import Any

import httpx
import pytest

from app.agent.model import (
    GeminiModel,
    ModelAPIError,
    build_model,
    from_openai_response,
    to_openai_messages,
    to_openai_tools,
)
from app.agent.tools import tool_definitions
from app.core.settings import Settings


def gemini_settings() -> Settings:
    return Settings(agent_provider="gemini", gemini_api_key="test-key", gemini_model="gemini-x")


def completion(message: dict[str, Any], finish: str = "stop") -> dict[str, Any]:
    return {
        "model": "gemini-x",
        "choices": [{"message": message, "finish_reason": finish}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }


def test_tools_drop_additional_properties() -> None:
    tools = to_openai_tools(tool_definitions())
    assert {t["type"] for t in tools} == {"function"}
    assert "additionalProperties" not in json.dumps(tools)
    triage = next(t for t in tools if t["function"]["name"] == "propose_triage")
    assert triage["function"]["parameters"]["required"] == ["category", "priority", "reason"]


def test_messages_map_tool_round_trip() -> None:
    messages = [
        {"role": "user", "content": "Ticket text"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Looking up"},
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "get_customer",
                    "input": {"email": "a@b.co"},
                    "extra_content": {"google": {"thought_signature": "sig"}},
                },
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": '{"found": true}'}],
        },
        {"role": "user", "content": "Finish the proposals."},
    ]
    out = to_openai_messages("System", messages)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "user"]
    call = out[2]["tool_calls"][0]
    assert out[2]["content"] == "Looking up"
    assert json.loads(call["function"]["arguments"]) == {"email": "a@b.co"}
    assert call["extra_content"] == {"google": {"thought_signature": "sig"}}
    assert out[3] == {"role": "tool", "tool_call_id": "c1", "content": '{"found": true}'}


def test_response_with_tool_calls_becomes_tool_use() -> None:
    turn = from_openai_response(
        completion(
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "",
                        "type": "function",
                        "function": {"name": "search_kb", "arguments": '{"query": "refund"}'},
                    }
                ],
            },
            finish="stop",
        )
    )
    assert turn.stop_reason == "tool_use"
    assert turn.tool_uses[0]["name"] == "search_kb"
    assert turn.tool_uses[0]["input"] == {"query": "refund"}
    assert turn.tool_uses[0]["id"].startswith("call_")
    assert (turn.input_tokens, turn.output_tokens) == (120, 30)


@pytest.mark.parametrize(("finish", "expected"), [("length", "max_tokens"), ("stop", "end_turn")])
def test_finish_reason_mapping(finish: str, expected: str) -> None:
    turn = from_openai_response(completion({"content": "Done."}, finish=finish))
    assert turn.stop_reason == expected
    assert turn.text == "Done."


async def test_create_retries_rate_limit_then_succeeds() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json=completion({"content": "Done."}))

    model = GeminiModel(
        gemini_settings(), transport=httpx.MockTransport(handler), retry_delays=(0.0,)
    )
    turn = await model.create("System", tool_definitions(), [{"role": "user", "content": "Hi"}])
    assert turn.text == "Done."
    assert len(calls) == 2
    assert calls[0].headers["Authorization"] == "Bearer test-key"
    body = json.loads(calls[0].content)
    assert body["model"] == "gemini-x"
    assert body["messages"][0] == {"role": "system", "content": "System"}


async def test_create_raises_model_error_on_client_error() -> None:
    model = GeminiModel(
        gemini_settings(),
        transport=httpx.MockTransport(lambda _: httpx.Response(400, text="bad key")),
        retry_delays=(),
    )
    with pytest.raises(ModelAPIError, match="400"):
        await model.create("System", [], [{"role": "user", "content": "Hi"}])


async def test_daily_quota_switches_model_and_replaces_foreign_signature() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if body["model"] == "first":
            return httpx.Response(429, text='{"quotaId": "GenerateRequestsPerDayPerProject"}')
        return httpx.Response(200, json=completion({"content": "Done."}))

    settings = Settings(agent_provider="gemini", gemini_api_key="k", gemini_model="first, second")
    model = GeminiModel(settings, transport=httpx.MockTransport(handler), retry_delays=(0.0,))
    history = [
        {"role": "user", "content": "Hi"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "c1",
                    "name": "search_kb",
                    "input": {"query": "x"},
                    "source_model": "first",
                    "extra_content": {"google": {"thought_signature": "real"}},
                }
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}"}],
        },
    ]
    turn = await model.create("System", [], history)
    assert turn.text == "Done."
    assert [b["model"] for b in seen] == ["first", "second"]
    call = seen[1]["messages"][2]["tool_calls"][0]
    assert (
        call["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
    )
    await model.create("System", [], [{"role": "user", "content": "Again"}])
    assert [b["model"] for b in seen] == ["first", "second", "second"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ('{"retryDelay": "29s"}', 29.0),
        ('{"retryDelay": "300s"}', 60.0),
        ('{"retryDelay": "0.5s"}', 2.0),
        ("{}", 2.0),
    ],
)
def test_retry_delay_follows_server_hint_within_bounds(body: str, expected: float) -> None:
    from app.agent.model import _retry_delay

    assert _retry_delay(httpx.Response(429, text=body), 2.0) == expected


def test_build_model_picks_provider() -> None:
    assert isinstance(build_model(gemini_settings()), GeminiModel)
    assert build_model(gemini_settings()).name == "gemini-x"
