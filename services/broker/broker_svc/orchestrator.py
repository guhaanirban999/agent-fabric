"""Broker orchestration pipeline.

submit -> retrieve candidates (registry, health=up, optional domain) -> LLM route
-> dispatch best-first through the gateways (next-best retry on failure) -> persist.
Every step shares one OTel trace so the whole task is traceable end-to-end.

The core runs in `_execute(submission, emit)`, calling `emit(event)` at each stage.
`run()` passes a no-op emit (synchronous result); `/tasks/stream` passes a queue
emit to surface progress over SSE — one code path, no duplication.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from fabric_common.models import (
    AgentEntry,
    HealthStatus,
    TaskRecord,
    TaskState,
    TaskSubmission,
)
from fabric_common.registry_client import RegistryClient
from fabric_common.telemetry import get_tracer
from fabric_common.telemetry.otel import current_trace_id_hex
from broker_svc.dispatch import dispatch_best_first
from broker_svc.router import Router
from broker_svc.store import TaskStore

logger = logging.getLogger(__name__)
tracer = get_tracer("broker")

Emit = Callable[[dict], Awaitable[None]]


async def _noop(_: dict) -> None:
    return None


class Orchestrator:
    def __init__(
        self,
        registry: RegistryClient,
        router: Router,
        store: TaskStore,
        gateway_mcp_url: str,
        gateway_a2a_url: str,
    ) -> None:
        self._registry = registry
        self._router = router
        self._store = store
        self._gateway_mcp_url = gateway_mcp_url
        self._gateway_a2a_url = gateway_a2a_url

    async def run(self, submission: TaskSubmission) -> TaskRecord:
        return await self._execute(submission, _noop)

    async def _execute(self, submission: TaskSubmission, emit: Emit) -> TaskRecord:
        with tracer.start_as_current_span("broker.task") as span:
            task = TaskRecord(submission=submission, state=TaskState.ROUTING)
            task.trace_id = current_trace_id_hex()
            task.domain = submission.domain
            await self._store.upsert(task)
            await emit({"event": "accepted", "task_id": str(task.id), "trace_id": task.trace_id})

            candidates = await self._candidates(submission.domain)
            span.set_attribute("broker.candidate_count", len(candidates))
            await emit({"event": "candidates", "count": len(candidates)})
            if not candidates:
                return await self._fail(task, "no healthy candidates found", emit)

            routes = await self._router.route(submission.task_text, candidates)
            if not routes:
                return await self._fail(task, "router produced no valid route", emit)
            task.decision = routes[0]
            await emit(
                {
                    "event": "routed",
                    "agent_id": routes[0].agent_id,
                    "skill_id": routes[0].skill_id,
                    "candidates_considered": len(routes),
                }
            )

            outcome = await dispatch_best_first(
                candidates,
                routes,
                submission.task_text,
                self._gateway_mcp_url,
                self._gateway_a2a_url,
                on_attempt=lambda c, r: emit(
                    {"event": "dispatching", "agent": c.name, "kind": c.kind.value}
                ),
                on_retry=lambda c, e: emit({"event": "retry", "agent": c.name, "error": e}),
            )
            if not outcome.ok:
                return await self._fail(task, outcome.error or "no route succeeded", emit)

            task.decision = outcome.route
            task.result = outcome.result
            task.state = TaskState.COMPLETED
            span.set_attribute("broker.selected_agent", outcome.cand.name)
            await self._store.upsert(task)
            await emit({"event": "completed", "agent": outcome.cand.name, "result": outcome.result})
            return task

    async def _candidates(self, domain: str | None) -> list[AgentEntry]:
        return await self._registry.list_agents(domain=domain, health=HealthStatus.UP)

    async def _fail(self, task: TaskRecord, reason: str, emit: Emit) -> TaskRecord:
        task.state = TaskState.FAILED
        task.error = reason
        await self._store.upsert(task)
        await emit({"event": "failed", "error": reason})
        logger.info("task %s failed: %s", task.id, reason)
        return task
