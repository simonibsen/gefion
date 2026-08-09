"""
Tests for bounding BatchSpanProcessor export size (#212).

The OTLP gRPC exporter rejects export messages larger than the collector's
4 MiB receive limit (RESOURCE_EXHAUSTED). The SDK's default
max_export_batch_size (512) lets span-heavy long jobs build export batches
over that limit, silently dropping spans for exactly the longest jobs we
most want to profile. observability.py must configure BatchSpanProcessor
with a bounded max_export_batch_size.
"""
import os
from unittest.mock import patch, MagicMock

import gefion.observability as obs


def test_batch_span_processor_configured_with_bounded_batch_size():
    """BatchSpanProcessor must be constructed with an explicit
    max_export_batch_size no larger than 128, so export messages stay
    comfortably under the 4 MiB OTLP gRPC receive limit."""
    original_enabled = obs.OTEL_ENABLED
    original_initialized = obs._otel_initialized

    try:
        obs.OTEL_ENABLED = False
        obs._otel_initialized = False

        mock_processor_cls = MagicMock()
        with patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", mock_processor_cls):
            with patch.dict(os.environ, {"OTEL_ENABLED": "true", "OTEL_EXPORTER": "console"}):
                result = obs.reinitialize()

        assert result is True
        assert mock_processor_cls.called, "BatchSpanProcessor was not constructed"
        _, kwargs = mock_processor_cls.call_args
        assert "max_export_batch_size" in kwargs, (
            "BatchSpanProcessor must be constructed with an explicit "
            "max_export_batch_size to stay under the OTLP gRPC 4 MiB message limit"
        )
        assert kwargs["max_export_batch_size"] <= 128
    finally:
        obs.OTEL_ENABLED = original_enabled
        obs._otel_initialized = original_initialized


def test_max_export_batch_size_overridable_via_env():
    """OTEL_MAX_EXPORT_BATCH_SIZE overrides the default bound."""
    original_enabled = obs.OTEL_ENABLED
    original_initialized = obs._otel_initialized

    try:
        obs.OTEL_ENABLED = False
        obs._otel_initialized = False

        mock_processor_cls = MagicMock()
        with patch("opentelemetry.sdk.trace.export.BatchSpanProcessor", mock_processor_cls):
            with patch.dict(
                os.environ,
                {
                    "OTEL_ENABLED": "true",
                    "OTEL_EXPORTER": "console",
                    "OTEL_MAX_EXPORT_BATCH_SIZE": "64",
                },
            ):
                result = obs.reinitialize()

        assert result is True
        _, kwargs = mock_processor_cls.call_args
        assert kwargs["max_export_batch_size"] == 64
    finally:
        obs.OTEL_ENABLED = original_enabled
        obs._otel_initialized = original_initialized
