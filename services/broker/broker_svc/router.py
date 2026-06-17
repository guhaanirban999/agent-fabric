"""LLM router — picks the best-fit agent/tool for a task.

Uses the Anthropic SDK with a *forced* tool call so the model must return structured
routes. `agent_id` is constrained to the real candidate ids via an enum, and every
returned route is validated against the candidate set before dispatch — a hallucinated
id can never reach a gateway. Kept framework-agnostic so ADK/LangGraph can replace it.
"""

from __future__ import annotations

import json
import logging

from anthropic import AsyncAnthropic

from fabric_common.models import AgentEntry, RouteDecision

logger = logging.getLogger(__name__)


def _candidate_payload(candidates: list[AgentEntry]) -> list[dict]:
    out = []
    for c in candidates:
        out.append(
            {
                "agent_id": str(c.id),
                "kind": c.kind.value,
                "name": c.name,
                "domain": c.domain,
                "description": c.description,
                "skills": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "description": s.description,
                        "examples": s.examples,
                        # Exact argument contract — the router must match these names/types.
                        "input_schema": s.input_schema,
                    }
                    for s in c.skills
                ],
            }
        )
    return out


def _tool_schema(candidate_ids: list[str]) -> dict:
    return {
        "name": "select_routes",
        "description": (
            "Select the agents/tools best able to handle the user's task, ranked "
            "best-first. Only choose from the provided candidates. For each route, set "
            "skill_id to one of that candidate's skill ids, and arguments to the exact "
            "keyword arguments the skill/tool expects (infer from its description)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "routes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_id": {"type": "string", "enum": candidate_ids},
                            "skill_id": {"type": "string"},
                            "arguments": {"type": "object"},
                            "confidence": {"type": "number"},
                            "rationale": {"type": "string"},
                        },
                        "required": ["agent_id", "skill_id", "arguments"],
                    },
                }
            },
            "required": ["routes"],
        },
    }


class Router:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model

    async def route(
        self,
        task_text: str,
        candidates: list[AgentEntry],
        history: list[dict] | None = None,
        *,
        force: bool = True,
    ) -> list[RouteDecision]:
        """Pick routes for a task.

        `force=True` (the /tasks path) forces a tool call — always returns the best
        route(s). `force=False` (the chat path) uses tool_choice=auto so smalltalk /
        no-tool-needed messages return [] (no tool_use block), letting the caller answer
        conversationally. `history` (recent {role,content} turns) lets follow-ups resolve.
        """
        if not candidates:
            return []
        ids = [str(c.id) for c in candidates]
        tool = _tool_schema(ids)
        payload = _candidate_payload(candidates)

        if force:
            system = (
                "You are the routing brain of an agent fabric. Given a task and a set of "
                "candidate agents/tools, choose which to invoke. Always call select_routes."
            )
        else:
            system = (
                "You are the routing brain of an agent fabric in a chat assistant. If the "
                "user's latest message needs one of the candidate tools/agents to answer, "
                "call select_routes. If it is smalltalk, a greeting, thanks, or answerable "
                "from the conversation without a tool, do NOT call any tool. Use the prior "
                "conversation to resolve references like 'that one' or 'what about #5'."
            )

        history_block = ""
        if history:
            rendered = "\n".join(f"{t['role']}: {t['content']}" for t in history)
            history_block = f"CONVERSATION SO FAR:\n{rendered}\n\n"

        user = (
            f"{history_block}USER MESSAGE:\n{task_text}\n\n"
            f"CANDIDATES (JSON):\n{json.dumps(payload, indent=2)}\n\n"
            "Pick the best route(s), ranked best-first, or none if no tool is needed."
        )

        tool_choice = (
            {"type": "tool", "name": "select_routes"} if force else {"type": "auto"}
        )
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
            tools=[tool],
            tool_choice=tool_choice,
        )

        routes = self._parse(resp, candidates)
        logger.info("router produced %d route(s) for msg=%r", len(routes), task_text[:60])
        return routes

    @staticmethod
    def _parse(resp, candidates: list[AgentEntry]) -> list[RouteDecision]:
        by_id = {str(c.id): c for c in candidates}
        block = next((b for b in resp.content if getattr(b, "type", "") == "tool_use"), None)
        if block is None:
            return []
        raw_routes = (block.input or {}).get("routes", [])

        validated: list[RouteDecision] = []
        for r in raw_routes:
            agent_id = r.get("agent_id")
            cand = by_id.get(agent_id)
            if cand is None:  # hallucinated id — drop it
                continue
            skill_id = r.get("skill_id")
            valid_skills = {s.id for s in cand.skills}
            if skill_id not in valid_skills:
                # Fall back to the candidate's first skill rather than trusting a bad id.
                skill_id = next(iter(valid_skills), skill_id)
            validated.append(
                RouteDecision(
                    agent_id=agent_id,
                    skill_id=skill_id,
                    arguments=r.get("arguments") or {},
                    confidence=float(r.get("confidence", 0.0) or 0.0),
                    rationale=r.get("rationale", ""),
                )
            )
        return validated
