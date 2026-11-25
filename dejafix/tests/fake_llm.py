"""A scripted stand-in for Claude: deterministic outputs, recorded calls, fixed token usage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from dejafix.agents.schemas import ProposedFix, ReuseCheck, ReviewVerdict, TriageResult
from dejafix.llm import LLMError, LLMResult, LLMUsage

# Token costs per schema, shaped like real usage: solving reads documentation and costs most.
TOKENS = {
    "TriageResult": (900, 150),
    "ReuseCheck": (700, 200),
    "ProposedFix": (3200, 700),
    "ReviewVerdict": (1600, 150),
}

PAYOUT_SIGNATURE = "Card payout still pending because payouts settle on T+2 business days excluding weekends"


@dataclass
class LLMCall:
    schema: str
    effort: str
    prompt: str
    system: str


@dataclass
class ScriptedLLM:
    events: list[str] = field(default_factory=list)
    calls: list[LLMCall] = field(default_factory=list)
    signature: str = PAYOUT_SIGNATURE
    contains_instructions: bool = False
    reuse_applies: bool = True
    review_verdict: str = "approve_for_human"
    solve_confidence: float = 0.9
    fail_with: LLMError | None = None
    overrides: dict[str, Callable[[str], BaseModel]] = field(default_factory=dict)

    async def run(self, *, system: str, prompt: str, schema: type[Any], effort: str) -> LLMResult[Any]:
        name = schema.__name__
        self.events.append(f"llm:{name}")
        self.calls.append(LLMCall(schema=name, effort=effort, prompt=prompt, system=system))
        if self.fail_with is not None:
            raise self.fail_with
        output = self.overrides[name](prompt) if name in self.overrides else self._default(name)
        input_tokens, output_tokens = TOKENS[name]
        return LLMResult(output, LLMUsage("claude-opus-5-5", input_tokens, output_tokens, latency_ms=50))

    def _default(self, name: str) -> BaseModel:
        if name == "TriageResult":
            return TriageResult(
                category="payouts",
                problem_signature=self.signature,
                severity="normal",
                language="en",
                contains_agent_instructions=self.contains_instructions,
                reasoning="Payout pending right after a weekend.",
            )
        if name == "ReuseCheck":
            return ReuseCheck(applies=self.reuse_applies, reasoning="Same cause: weekend settlement.", customer_reply="Your payout arrives Tuesday.")
        if name == "ProposedFix":
            return ProposedFix(
                fix_summary="Explain the T+2 business day schedule and check Payouts > Calendar.",
                fix_steps=["Open Payouts > Calendar to see the expected date.", "Investigate only if still pending a day later."],
                customer_reply="Payouts arrive T+2 business days after capture.",
                confidence=self.solve_confidence,
                cited_sources=["payouts.md#payout-schedule"],
                needs_escalation=False,
                reasoning="The payouts doc defines T+2 business days.",
                plan=["Read the payout schedule", "Map the ticket dates to it"],
            )
        if name == "ReviewVerdict":
            return ReviewVerdict(verdict=self.review_verdict, policy_issues=[], reasoning="Follows the docs and policy.")
        raise AssertionError(f"unexpected schema {name}")

    def schemas(self) -> list[str]:
        return [call.schema for call in self.calls]
