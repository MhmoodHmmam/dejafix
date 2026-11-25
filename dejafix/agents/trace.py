"""Write each agent's decision into Meko as a conversation turn (with reasoning and plan) and
attribute its Claude token usage to that turn, so Meko's trace view answers "why?" and "how much?".

Meko scopes a conversation to one agent, so every agent gets its own conversation per ticket.
All of a ticket's conversations share the run_id `dejafix:<ticket>`. Fixed session_ids and
message seeds make a re-run reconnect to the same conversation and messages.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from dejafix.agents.schemas import clip
from dejafix.llm import LLMUsage
from dejafix.meko import MekoGateway, MekoToolError

MAX_TURN_CHARS = 4000
MAX_REASONING_CHARS = 600
MIN_PLAN_ITEMS = 2  # Meko expects 2 to 4 plan items, or none
MAX_PLAN_ITEMS = 4


def run_id_for(ticket_ref: str) -> str:
    return f"dejafix:{ticket_ref}"


class AgentTrace:
    def __init__(self, meko: MekoGateway, *, agent: str, ticket_ref: str) -> None:
        self._root = meko.as_agent(agent)
        self.agent = agent
        self.ticket_ref = ticket_ref
        self.meko: MekoGateway | None = None

    @property
    def conversation_id(self) -> str:
        return self.meko.conversation_id if self.meko and self.meko.conversation_id else ""

    async def open(self) -> MekoGateway:
        """Create (or reconnect to) this agent's conversation for the ticket."""
        reply = await self._root.call(
            "conversation_create",
            title=f"{self.ticket_ref} · {self.agent}",
            session_id=f"{run_id_for(self.ticket_ref)}:{self.agent}",
            run_id=run_id_for(self.ticket_ref),
            metadata=json.dumps({"ticket": self.ticket_ref, "app": "dejafix"}),
        )
        conversation_id = reply.get("id") if isinstance(reply, Mapping) else None
        if not isinstance(conversation_id, str) or not conversation_id:
            raise MekoToolError("conversation_create", "unexpected_reply", "no conversation id")
        self.meko = self._root.in_conversation(conversation_id)
        return self.meko

    async def record(
        self,
        step: str,
        *,
        input_text: str,
        output: Mapping[str, Any],
        reasoning: str,
        plan: Sequence[str] = (),
        usage: LLMUsage | None = None,
    ) -> str:
        meko = self.meko or await self.open()
        plan_items = [clip(item, 160) for item in plan if item.strip()][:MAX_PLAN_ITEMS]
        metadata: dict[str, Any] = {"ticket": self.ticket_ref, "step": step}
        if usage:
            metadata.update(model=usage.model, input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
        reply = await meko.call(
            "conversation_add_message",
            input=clip(input_text, MAX_TURN_CHARS),
            output=clip(json.dumps(output, ensure_ascii=False, default=str), MAX_TURN_CHARS),
            reasoning=clip(reasoning, MAX_REASONING_CHARS) or None,
            plan=plan_items if len(plan_items) >= MIN_PLAN_ITEMS else None,
            metadata=json.dumps(metadata),
            seed=f"{run_id_for(self.ticket_ref)}:{self.agent}:{step}",
        )
        message_id = str(reply.get("message_id", "")) if isinstance(reply, Mapping) else ""
        if usage:
            await meko.call(
                "track_token_usage",
                name=f"{self.agent}.{step}",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                model=usage.model,
                message_id=message_id or None,
            )
        return message_id
