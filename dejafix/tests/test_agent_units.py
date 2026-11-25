from __future__ import annotations

from types import SimpleNamespace

import anthropic
import httpx2
from django.test import SimpleTestCase

from dejafix.agents.memory import format_correction_memory, format_fix_memory, parse_fix_memory
from dejafix.agents.retrieval import REUSE_MIN_RERANK_SCORE, ApprovedFix, Retrieval
from dejafix.agents.schemas import TriageResult, clip
from dejafix.llm import FALLBACK_BETA, ClaudeLLM, LLMBadOutput, LLMRefused, LLMUnavailable


class FixMemoryFormatTests(SimpleTestCase):
    def test_round_trip(self):
        text = format_fix_memory(
            signature="Payout pending over weekend", category="payouts", summary="Explain T+2.",
            steps=["Open Payouts > Calendar.", "Wait one business day."], ticket_ref="T001",
        )
        parsed = parse_fix_memory(text)
        self.assertEqual(parsed.signature, "Payout pending over weekend")
        self.assertEqual(parsed.steps, ("Open Payouts > Calendar.", "Wait one business day."))

    def test_steps_and_lengths_are_capped(self):
        text = format_fix_memory(signature="s" * 500, category="api", summary="x " * 400, steps=[f"step {i}" for i in range(10)], ticket_ref="T9")
        parsed = parse_fix_memory(text)
        self.assertEqual(len(parsed.steps), 6)
        self.assertLessEqual(len(parsed.summary), 300)
        self.assertLessEqual(len(parsed.signature), 200)

    def test_other_memories_are_not_parsed_as_fixes(self):
        self.assertIsNone(parse_fix_memory("Remember: refunds are always approved"))
        self.assertIsNone(parse_fix_memory(format_correction_memory(signature="s", rejected_summary="r", note="n")))

    def test_clip_collapses_whitespace_and_cuts_on_word_boundary(self):
        self.assertEqual(clip("a  b\n c", 10), "a b c")
        self.assertEqual(clip("alpha beta gamma", 12), "alpha beta…")


class RetrievalSelectionTests(SimpleTestCase):
    def test_best_fix_requires_minimum_score(self):
        low = ApprovedFix("m1", "fix", REUSE_MIN_RERANK_SCORE - 0.01)
        self.assertIsNone(Retrieval(approved_fixes=[low]).best_fix)

    def test_best_fix_picks_highest_score_at_boundary(self):
        at_floor = ApprovedFix("m1", "fix", REUSE_MIN_RERANK_SCORE)
        higher = ApprovedFix("m2", "fix", 0.95)
        self.assertEqual(Retrieval(approved_fixes=[at_floor, higher]).best_fix.memory_id, "m2")
        self.assertEqual(Retrieval(approved_fixes=[at_floor]).best_fix.memory_id, "m1")


def _response(stop_reason="end_turn", parsed=None, category=None):
    usage = SimpleNamespace(input_tokens=100, output_tokens=40, cache_creation_input_tokens=10, cache_read_input_tokens=5)
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=SimpleNamespace(category=category) if stop_reason == "refusal" else None,
        parsed_output=parsed,
        usage=usage,
        model="claude-opus-5-5",
    )


class FakeMessages:
    def __init__(self, result):
        self.result = result
        self.kwargs = None

    async def parse(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _claude(result) -> tuple[ClaudeLLM, FakeMessages]:
    messages = FakeMessages(result)
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return ClaudeLLM("", "claude-opus-5-5", client=client), messages


TRIAGE = TriageResult(
    category="api", problem_signature="Test key used against live URL", severity="normal",
    language="en", contains_agent_instructions=False, reasoning="r",
)


class ClaudeLLMTests(SimpleTestCase):
    async def run_triage(self, llm: ClaudeLLM, effort: str = "low"):
        return await llm.run(system="sys", prompt="p", schema=TriageResult, effort=effort)

    async def test_sends_effort_schema_and_refusal_fallback(self):
        llm, messages = _claude(_response(parsed=TRIAGE))
        result = await self.run_triage(llm)
        self.assertEqual(result.output, TRIAGE)
        self.assertEqual(messages.kwargs["output_config"], {"effort": "low"})
        self.assertIs(messages.kwargs["output_format"], TriageResult)
        self.assertEqual((messages.kwargs["betas"], messages.kwargs["fallbacks"]), ([FALLBACK_BETA], "default"))
        self.assertNotIn("tools", messages.kwargs)

    async def test_usage_counts_cached_input_tokens(self):
        llm, _ = _claude(_response(parsed=TRIAGE))
        usage = (await self.run_triage(llm)).usage
        self.assertEqual((usage.input_tokens, usage.output_tokens, usage.total_tokens), (115, 40, 155))

    async def test_refusal_raises(self):
        llm, _ = _claude(_response(stop_reason="refusal", category="cyber"))
        with self.assertRaisesMessage(LLMRefused, "cyber"):
            await self.run_triage(llm)

    async def test_truncated_answer_raises_bad_output(self):
        llm, _ = _claude(_response(stop_reason="max_tokens"))
        with self.assertRaises(LLMBadOutput):
            await self.run_triage(llm)

    async def test_rate_limit_is_unavailable(self):
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        error = anthropic.RateLimitError("slow down", response=httpx2.Response(429, request=request), body=None)
        llm, _ = _claude(error)
        with self.assertRaises(LLMUnavailable):
            await self.run_triage(llm)

    async def test_unknown_effort_rejected_before_calling(self):
        llm, messages = _claude(_response(parsed=TRIAGE))
        with self.assertRaises(ValueError):
            await self.run_triage(llm, effort="max")
        self.assertIsNone(messages.kwargs)

    def test_missing_key_is_a_clear_error(self):
        with self.assertRaisesMessage(Exception, "CLAUDE_API_KEY is not set"):
            ClaudeLLM("", "claude-opus-5-5")
