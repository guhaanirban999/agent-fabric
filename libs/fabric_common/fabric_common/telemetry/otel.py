"""OpenTelemetry bootstrap shared by every service.

A single trace id flows registry -> broker -> gateway -> downstream, which is what
makes the (deferred) Visualizer a drop-in later: the spans are already being emitted.
"""

from __future__ import annotations

import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)

_CONFIGURED = False


def setup_telemetry(service_name: str, otlp_endpoint: str, fastapi_app=None) -> None:
    """Configure the global tracer provider and (optionally) instrument a FastAPI app.

    Safe to call once per process. `otlp_endpoint` is the collector's gRPC endpoint.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    # Only export when a collector endpoint is configured. Empty => tracing stays
    # in-process (spans + trace ids still work; nothing tries to connect). This lets
    # the stack run without an otel-collector when monitoring isn't in use.
    if otlp_endpoint and otlp_endpoint.strip():
        try:
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            provider.add_span_processor(BatchSpanProcessor(exporter))
        except Exception as exc:  # pragma: no cover - never block startup on telemetry
            logger.warning("OTLP exporter init failed (%s); traces stay in-process", exc)
    else:
        logger.info("No OTLP endpoint set; tracing runs in-process (no export)")

    trace.set_tracer_provider(provider)

    if fastapi_app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

            FastAPIInstrumentor.instrument_app(fastapi_app)
        except Exception as exc:  # pragma: no cover
            logger.warning("FastAPI instrumentation failed: %s", exc)

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        logger.warning("httpx instrumentation failed: %s", exc)

    _CONFIGURED = True
    logger.info("Telemetry configured for service=%s", service_name)


def get_tracer(name: str = "agent-fabric") -> Tracer:
    return trace.get_tracer(name)


def current_trace_id_hex() -> str | None:
    """Hex trace id of the active span, for stamping audit/task records."""
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.trace_id:
        return format(ctx.trace_id, "032x")
    return None
