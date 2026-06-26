from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.system_metrics import SystemMetricsInstrumentor


def setup_telemetry():
    resource = Resource.create({"service.name": "drestly-image-processing"})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    reader = PeriodicExportingMetricReader(OTLPMetricExporter())  # exports ~every 60s
    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[reader])
    )

    # Host/process health metrics. Must run AFTER set_meter_provider — the
    # instrumentor binds observable callbacks to the global meter provider now.
    # Memory is the signal that matters: BiRefNet inference is what OOM-kills this
    # service, so process RSS vs the host's free memory is what to watch. Scoped to
    # a low-cardinality set (process + system memory/CPU); everything else omitted.
    SystemMetricsInstrumentor(
        config={
            "process.memory.usage": None,      # RSS bytes — the OOM signal
            "process.memory.virtual": None,    # VMS bytes
            "process.cpu.time": ["user", "system"],
            "process.cpu.utilization": ["user", "system"],
            "system.memory.usage": ["used", "free", "cached"],
            "system.cpu.utilization": ["idle", "user", "system", "irq"],
        }
    ).instrument()
