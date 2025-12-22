"""
OpenTelemetry instrumentation module for g2.

This module provides toggle-able observability via OpenTelemetry exported to Grafana Tempo (OTLP).
Enable by setting OTEL_ENABLED=true in your environment.

Usage:
    from g2.observability import tracer, create_span

    # Using context manager
    with create_span("operation_name", key="value"):
        do_work()

    # Manual span management
    span = tracer.start_span("operation_name")
    span.set_attribute("key", "value")
    try:
        do_work()
    finally:
        span.end()

Configuration:
    OTEL_ENABLED: Enable/disable OpenTelemetry (default: false)
    OTEL_SERVICE_NAME: Service name for traces (default: g2)
    OTEL_EXPORTER: Exporter type - otlp or console (default: otlp)
    OTEL_OTLP_ENDPOINT: OTLP endpoint (default: http://localhost:4317)
    OTEL_SAMPLING_RATE: Sampling rate 0.0-1.0 (default: 1.0)
"""

import os
import logging
from contextlib import contextmanager
from typing import Optional, Any, Dict

# Check if OTEL is enabled before importing heavy dependencies
OTEL_ENABLED = os.getenv("OTEL_ENABLED", "false").lower() in ("true", "1", "yes")

if OTEL_ENABLED:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    # Console exporter for debugging
    try:
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter
    except ImportError:
        ConsoleSpanExporter = None
else:
    trace = None

logger = logging.getLogger(__name__)


class NoOpSpan:
    """No-op span for when OTEL is disabled - zero overhead."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_attributes(self, attributes: Dict[str, Any]) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def end(self) -> None:
        pass


class NoOpTracer:
    """No-op tracer for when OTEL is disabled - zero overhead."""

    def start_span(self, name: str, context: Any = None, kind: Any = None,
                   attributes: Optional[Dict[str, Any]] = None) -> NoOpSpan:
        return NoOpSpan()

    def start_as_current_span(self, name: str, context: Any = None, kind: Any = None,
                              attributes: Optional[Dict[str, Any]] = None):
        return NoOpSpan()


def _initialize_otel() -> bool:
    """Initialize OpenTelemetry with configured exporter."""
    if not OTEL_ENABLED:
        logger.info("OpenTelemetry is disabled (OTEL_ENABLED=false)")
        return False

    try:
        service_name = os.getenv("OTEL_SERVICE_NAME", "g2")
        exporter_type = os.getenv("OTEL_EXPORTER", "otlp").lower()
        sampling_rate = float(os.getenv("OTEL_SAMPLING_RATE", "1.0"))
    except Exception as e:
        logger.error(f"Failed to parse OpenTelemetry configuration: {e}")
        return False

    # Create resource
    resource = Resource(attributes={
        SERVICE_NAME: service_name
    })

    # Create sampler
    sampler = TraceIdRatioBased(sampling_rate)

    # Create tracer provider
    provider = TracerProvider(resource=resource, sampler=sampler)

    # Configure exporter
    if exporter_type == "otlp":
        otlp_endpoint_raw = os.getenv("OTEL_OTLP_ENDPOINT", "http://localhost:4317").strip()
        insecure = otlp_endpoint_raw.startswith("http://")
        otlp_endpoint = otlp_endpoint_raw.removeprefix("http://").removeprefix("https://")
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=insecure)
        logger.info(f"OpenTelemetry initialized with OTLP exporter: {otlp_endpoint_raw}")
    elif exporter_type == "console":
        if ConsoleSpanExporter:
            exporter = ConsoleSpanExporter()
            logger.info("OpenTelemetry initialized with Console exporter")
        else:
            logger.error("Console exporter not available")
            return False
    else:
        logger.error(f"Unknown exporter type: {exporter_type}")
        return False

    # Add span processor
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)

    # Set global tracer provider
    trace.set_tracer_provider(provider)

    # Auto-instrument psycopg for database tracing
    try:
        PsycopgInstrumentor().instrument()
        logger.info("Psycopg auto-instrumentation enabled")
    except Exception as e:
        logger.warning(f"Failed to auto-instrument psycopg: {e}")

    logger.info(f"OpenTelemetry enabled: service={service_name}, "
                f"exporter={exporter_type}, sampling={sampling_rate}")

    return True  # Return success flag instead of tracer


# Initialize OpenTelemetry (will be no-op if OTEL_ENABLED=false)
_otel_initialized = False
_otel_shutdown_called = False

if OTEL_ENABLED:
    _otel_initialized = _initialize_otel()
    if not _otel_initialized:
        # Fallback to no-op if initialization failed
        OTEL_ENABLED = False


@contextmanager
def create_span(name: str, **attributes):
    """
    Create a traced span with automatic exception recording.

    Usage:
        with create_span("compute_features", symbol="AAPL", workers=4):
            compute_features(...)

    Args:
        name: Span name
        **attributes: Key-value pairs to attach as span attributes
    """
    if not OTEL_ENABLED:
        yield NoOpSpan()
        return

    # Get tracer from the global provider each time to ensure proper context
    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span(name) as span:
        # Set attributes
        for key, value in attributes.items():
            # Convert to string for complex types
            if isinstance(value, (list, dict, set)):
                value = str(value)
            span.set_attribute(key, value)

        # Log span creation for verification
        span_context = span.get_span_context()
        logger.info(f"Created span '{name}' - trace_id: {format(span_context.trace_id, '032x')}, span_id: {format(span_context.span_id, '016x')}")

        try:
            yield span
        except Exception as e:
            # Record exception in span
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise


def add_event(span, name: str, **attributes):
    """
    Add an event to a span.

    Args:
        span: The span to add the event to
        name: Event name
        **attributes: Key-value pairs for event attributes
    """
    if OTEL_ENABLED and span is not None:
        span.add_event(name, attributes=attributes)


def set_attributes(span, **attributes):
    """
    Set multiple attributes on a span.

    Args:
        span: The span to set attributes on
        **attributes: Key-value pairs to set as attributes
    """
    if OTEL_ENABLED and span is not None:
        for key, value in attributes.items():
            if isinstance(value, (list, dict, set)):
                value = str(value)
            span.set_attribute(key, value)


def get_current_span():
    """Get the current active span from context."""
    if not OTEL_ENABLED:
        return NoOpSpan()
    return trace.get_current_span()


def is_enabled() -> bool:
    """Check if OpenTelemetry is enabled."""
    return OTEL_ENABLED


def shutdown():
    """Shutdown OpenTelemetry and flush remaining spans."""
    global _otel_shutdown_called

    # Prevent duplicate shutdowns
    if _otel_shutdown_called:
        logger.info("OpenTelemetry shutdown already called, skipping")
        return

    if OTEL_ENABLED and trace:
        try:
            provider = trace.get_tracer_provider()
            if hasattr(provider, 'shutdown'):
                logger.info("Shutting down OpenTelemetry and flushing spans...")
                provider.shutdown()
                logger.info("OpenTelemetry shutdown complete - spans flushed")
                _otel_shutdown_called = True
            else:
                logger.warning("Tracer provider doesn't have shutdown method")
        except Exception as e:
            logger.error(f"Error during OpenTelemetry shutdown: {e}")
    else:
        logger.info(f"Shutdown called but OTEL not active (OTEL_ENABLED={OTEL_ENABLED})")
