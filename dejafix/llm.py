"""Structured Claude calls: one request in, one validated Pydantic object and its token usage out.

The agents never get tools. Each one returns a typed object, so text inside a ticket can't
make the model call anything (prompt injection has nothing to act on).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

import anthropic
import pydantic

logger = logging.getLogger(__name__)

OutputT = TypeVar("OutputT", bound=pydantic.BaseModel)

# On a policy refusal the API retries the same request on the model's default fallback.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_OUTPUT_TOKENS = 16000
REQUEST_TIMEOUT_SECONDS = 120.0
MAX_RETRIES = 2
EFFORT_LEVELS = frozenset({"low", "medium", "high"})


class LLMError(Exception):
    """Claude could not produce a usable answer."""


class LLMUnavailable(LLMError):
    """Network failure, rate limit, 5xx or rejected key. The ticket can be retried later."""


class LLMRefused(LLMError):
    """The model (and its fallback) declined the request."""


class LLMBadOutput(LLMError):
    """The answer was cut off or did not match the expected schema."""


@dataclass(frozen=True)
class LLMUsage:
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LLMResult(Generic[OutputT]):
    output: OutputT
    usage: LLMUsage


class StructuredLLM(Protocol):
    async def run(
        self, *, system: str, prompt: str, schema: type[OutputT], effort: str
    ) -> LLMResult[OutputT]: ...


class ClaudeLLM:
    def __init__(self, api_key: str, model: str, *, client: anthropic.AsyncAnthropic | None = None) -> None:
        if not api_key and client is None:
            raise LLMError("CLAUDE_API_KEY is not set. Add it to .env (see .env.example).")
        self.model = model
        self._client = client or anthropic.AsyncAnthropic(
            api_key=api_key, timeout=REQUEST_TIMEOUT_SECONDS, max_retries=MAX_RETRIES
        )

    @classmethod
    def from_settings(cls) -> ClaudeLLM:
        from django.conf import settings

        return cls(settings.ANTHROPIC_API_KEY, settings.DEJAFIX_MODEL)

    async def run(
        self, *, system: str, prompt: str, schema: type[OutputT], effort: str
    ) -> LLMResult[OutputT]:
        if effort not in EFFORT_LEVELS:
            raise ValueError(f"effort must be one of {sorted(EFFORT_LEVELS)}")
        started = time.perf_counter()
        try:
            response = await self._client.beta.messages.parse(
                model=self.model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_format=schema,
                output_config={"effort": effort},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMUnavailable("Claude rejected the API key.") from exc
        except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError) as exc:
            raise LLMUnavailable(f"Claude is unavailable ({type(exc).__name__}).") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}.") from exc
        except pydantic.ValidationError as exc:
            raise LLMBadOutput(f"Answer did not match {schema.__name__}.") from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.stop_reason == "refusal":
            category = response.stop_details.category if response.stop_details else None
            raise LLMRefused(f"Claude declined the request (category: {category}).")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise LLMBadOutput(f"Answer was incomplete (stop_reason: {response.stop_reason}).")

        usage = response.usage
        input_tokens = (
            usage.input_tokens
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        return LLMResult(
            output=response.parsed_output,
            usage=LLMUsage(
                model=response.model,
                input_tokens=input_tokens,
                output_tokens=usage.output_tokens,
                latency_ms=latency_ms,
            ),
        )
