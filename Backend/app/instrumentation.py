from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
import os


def setup_opentelemetry(app):
    """
    Initialize OTel tracing + metrics for the FastAPI app.

    Call this AFTER app is created but BEFORE uvicorn starts serving.
    Skips instrumentation when ENV=testing (tests don't need a Collector).
    """
    if os.getenv("ENV") == "testing":
        return app

    service_name = os.getenv("OTEL_SERVICE_NAME", "score-leaderboard-backend")
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    # ── Resource ──
    # A Resource is metadata attached to ALL telemetry from this service.
    # It's how you identify which service produced a span/metric/log.
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: "0.1.0",
        "deployment.environment": os.getenv("ENV", "development"),
    })

    # ── Tracing ──
    # TracerProvider is the global factory for Tracers.
    # A SpanProcessor decides what happens to spans after they're created.
    # BatchSpanProcessor batches spans and sends them in bulk — better throughput. default 512 spans or 5 seconds configurable.
    # SimpleSpanProcessor sends each span immediately — better for debugging, worse for perf.
    trace_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    sampler = TraceIdRatioBased(rate=float(os.getenv("OTEL_TRACING_SAMPLING_RATE", "1.0")))
    tracer_provider = TracerProvider(resource=resource, sampler=sampler)
    tracer_provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    trace.set_tracer_provider(tracer_provider)

    # ── Metrics ──
    # PeriodicExportingMetricReader collects metrics on a timer (default: 60s)
    # and pushes them via OTLP. Alternative: pull-based (Prometheus scrape),
    # but that only works for metrics, not traces/logs.
    metric_exporter = OTLPMetricExporter(endpoint=otlp_endpoint, insecure=True)
    metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=int(
        os.getenv("OTEL_METRIC_EXPORT_INTERVAL_MS", "60000")
    ))
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # ── Auto-instrument FastAPI ──
    # This patches the ASGI app to automatically create spans for:
    #   - Every incoming HTTP request (method, path, status, duration)
    #   - Unhandled exceptions
    # It also sets the span name to the route pattern (e.g. "/events/{event_id}")
    # rather than the literal URL, which makes aggregation actually useful.
    FastAPIInstrumentor.instrument_app(app)

    return app


def teardown_opentelemetry():
    """
    Gracefully flush pending telemetry on shutdown.
    Without this, in-flight spans/metrics may be lost.
    """
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        provider.shutdown()

    m_provider = metrics.get_meter_provider()
    if isinstance(m_provider, MeterProvider):
        m_provider.shutdown()