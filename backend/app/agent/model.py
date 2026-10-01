from dataclasses import dataclass, field
from typing import Any, Protocol, cast

import anthropic
from anthropic.types.beta import BetaMessageParam, BetaOutputConfigParam, BetaToolParam

from app.core.settings import Settings


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
