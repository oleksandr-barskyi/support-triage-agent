import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import anthropic
import httpx
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaToolParam

from app.core.settings import Settings

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
RETRY_STATUSES = {429, 500, 502, 503, 504}
STOP_REASONS = {"tool_calls": "tool_use", "length": "max_tokens", "content_filter": "refusal"}
FOREIGN_SIGNATURE = "skip_thought_signature_validator"
UNAVAILABLE_COOLDOWN_S = 3600.0


class ModelAPIError(Exception):
    pass


@dataclass
class ModelTurn:
    content: list[dict[str, Any]]
    stop_reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    @property
    def text(self) -> str:
        return "\n".join(b["text"] for b in self.content if b.get("type") == "text" and b["text"])


class AgentModel(Protocol):
    name: str

    async def create(
        self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> ModelTurn: ...


@dataclass
class AnthropicModel:
    settings: Settings
    name: str = field(init=False)
    _client: anthropic.AsyncAnthropic = field(init=False)

    def __post_init__(self) -> None:
        self.name = self.settings.agent_model
        self._client = anthropic.AsyncAnthropic(
            api_key=self.settings.anthropic_api_key or None,
            timeout=self.settings.agent_timeout_seconds,
            max_retries=2,
        )

    async def create(
        self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> ModelTurn:
        fallbacks = self.settings.agent_fallbacks
        response = await self._client.beta.messages.create(
            model=self.name,
            max_tokens=16000,
            system=system,
            tools=cast("list[BetaToolParam]", tools),
            messages=cast("list[BetaMessageParam]", messages),
            output_config=cast("BetaOutputConfigParam", {"effort": self.settings.agent_effort}),
            betas=["server-side-fallback-2026-07-01"] if fallbacks else [],
            fallbacks="default" if fallbacks else anthropic.omit,
        )
        return ModelTurn(
            content=[block.model_dump(exclude_none=True) for block in response.content],
            stop_reason=response.stop_reason or "end_turn",
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=response.model,
        )


def _plain_schema(schema: Any) -> Any:
    if isinstance(schema, dict):
        return {k: _plain_schema(v) for k, v in schema.items() if k != "additionalProperties"}
    if isinstance(schema, list):
        return [_plain_schema(v) for v in schema]
    return schema


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": _plain_schema(t["input_schema"]),
            },
        }
        for t in tools
    ]


def _assistant_message(blocks: list[dict[str, Any]], model: str | None) -> dict[str, Any]:
    text = "\n".join(b["text"] for b in blocks if b.get("type") == "text" and b.get("text"))
    message: dict[str, Any] = {"role": "assistant", "content": text or None}
    calls = []
    for b in blocks:
        if b.get("type") != "tool_use":
            continue
        call: dict[str, Any] = {
            "id": b["id"],
            "type": "function",
            "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})},
        }
        if model is not None and b.get("source_model", model) != model:
            call["extra_content"] = {"google": {"thought_signature": FOREIGN_SIGNATURE}}
        elif "extra_content" in b:
            call["extra_content"] = b["extra_content"]
        calls.append(call)
    if calls:
        message["tool_calls"] = calls
    return message


def to_openai_messages(
    system: str, messages: list[dict[str, Any]], model: str | None = None
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            out.append({"role": m["role"], "content": content})
        elif m["role"] == "assistant":
            out.append(_assistant_message(content, model))
        else:
            for b in content:
                if b.get("type") == "tool_result":
                    out.append(
                        {"role": "tool", "tool_call_id": b["tool_use_id"], "content": b["content"]}
                    )
                elif b.get("type") == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out


def from_openai_response(data: dict[str, Any]) -> ModelTurn:
    choice = data["choices"][0]
    message = choice.get("message") or {}
    content: list[dict[str, Any]] = []
    if message.get("content"):
        content.append({"type": "text", "text": message["content"]})
    for call in message.get("tool_calls") or []:
        fn = call["function"]
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        block: dict[str, Any] = {
            "type": "tool_use",
            "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
            "name": fn["name"],
            "input": args,
        }
        if "extra_content" in call:
            block["extra_content"] = call["extra_content"]
        content.append(block)
    finish = choice.get("finish_reason") or "stop"
    stop_reason = STOP_REASONS.get(finish, "end_turn")
    if stop_reason == "end_turn" and any(b["type"] == "tool_use" for b in content):
        stop_reason = "tool_use"
    usage = data.get("usage") or {}
    return ModelTurn(
        content=content,
        stop_reason=stop_reason,
        input_tokens=usage.get("prompt_tokens", 0),
        output_tokens=usage.get("completion_tokens", 0),
        model=data.get("model") or "",
    )


class _ModelUnavailable(Exception):
    pass


@dataclass
class GeminiModel:
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None
    retry_delays: tuple[float, ...] = (2.0, 5.0, 10.0)
    name: str = field(init=False)
    models: list[str] = field(init=False)
    _unavailable_until: dict[str, float] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.models = [m.strip() for m in self.settings.gemini_model.split(",") if m.strip()]
        self.name = self.models[0]

    async def create(
        self, system: str, tools: list[dict[str, Any]], messages: list[dict[str, Any]]
    ) -> ModelTurn:
        async with httpx.AsyncClient(
            base_url=GEMINI_BASE_URL,
            timeout=self.settings.agent_timeout_seconds,
            transport=self.transport,
        ) as client:
            for model in self.models:
                if self._unavailable_until.get(model, 0.0) > time.monotonic():
                    continue
                try:
                    turn = await self._call(client, model, system, tools, messages)
                except _ModelUnavailable:
                    self._unavailable_until[model] = time.monotonic() + UNAVAILABLE_COOLDOWN_S
                    continue
                for block in turn.tool_uses:
                    block["source_model"] = model
                turn.model = turn.model or model
                return turn
        raise ModelAPIError("every configured Gemini model is out of quota or unavailable")

    async def _call(
        self,
        client: httpx.AsyncClient,
        model: str,
        system: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> ModelTurn:
        payload = {
            "model": model,
            "messages": to_openai_messages(system, messages, model),
            "tools": to_openai_tools(tools),
            "reasoning_effort": self.settings.gemini_reasoning_effort,
        }
        headers = {"Authorization": f"Bearer {self.settings.gemini_api_key}"}
        for attempt in range(len(self.retry_delays) + 1):
            try:
                response = await client.post("/chat/completions", json=payload, headers=headers)
            except httpx.HTTPError as exc:
                raise ModelAPIError(f"Gemini request failed: {type(exc).__name__}") from exc
            if response.status_code == 404 or (
                response.status_code == 429 and "PerDay" in response.text
            ):
                raise _ModelUnavailable(model)
            if response.status_code in RETRY_STATUSES and attempt < len(self.retry_delays):
                await asyncio.sleep(self.retry_delays[attempt])
                continue
            if response.status_code >= 400:
                detail = response.text[:300]
                raise ModelAPIError(f"Gemini HTTP {response.status_code}: {detail}")
            return from_openai_response(response.json())
        raise ModelAPIError("Gemini retries exhausted")


def build_model(settings: Settings) -> AgentModel:
    if settings.agent_provider == "gemini":
        return GeminiModel(settings)
    return AnthropicModel(settings)
