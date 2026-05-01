"""
Instruments the FastAPI application and SQLAlchemy engine 
with OpenTelemetry for metrics collection and exporting to an OTLP endpoint (sent to grafan cloud as of now).
"""
from fastapi import FastAPI
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

from app.config import config
from app.DB.main import engine


def parse_headers(raw: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" in pair:
            k, v = pair.split("=", 1)
            headers[k.strip()] = v.strip()
    return headers


def setup_telemetry(app: FastAPI) -> None:
    if not config.OTEL_ENABLED:
        return

    endpoint = config.OTEL_EXPORTER_OTLP_ENDPOINT
    headers = parse_headers(config.OTEL_EXPORTER_OTLP_HEADERS)
    service_name = config.OTEL_SERVICE_NAME

    resource = Resource.create({"service.name": service_name})

    exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=10_000)
    provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(provider)

    SQLAlchemyInstrumentor().instrument(engine=engine)
    FastAPIInstrumentor.instrument_app(app)
