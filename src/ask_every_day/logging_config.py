"""JSON logging for executable boundaries; importing libraries has no side effects."""

import json
import logging
import re
import sys
from datetime import UTC, datetime

_HANDLER_NAME = "ask_every_day_json_stderr"
_EVENT = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_TEXT_FIELDS = frozenset(
    {
        "checkpoint_phase",
        "resolution",
        "palette_colour",
        "post_id",
        "operation",
        "previous_state",
        "new_state",
        "template",
        "format",
        "output_path",
        "source_path",
        "config_path",
        "database_path",
        "error_type",
        "failure_category",
        "tunnel_transport",
    }
)
_INTEGER_FIELDS = frozenset(
    {
        "palette_sequence",
        "queue_position",
        "checkpoint_revision",
        "publication_id",
        "http_status",
        "width",
        "height",
        "line_count",
        "highlight_segment_count",
        "byte_size",
        "schema_version",
        "row_count",
        "imported_count",
        "unchanged_count",
        "updated_count",
        "rejected_count",
        "excluded_count",
        "queue_length",
        "seed",
        "post_count",
        "attempts",
        "attempt_count",
        "elapsed_ms",
        "tunnel_ready_ms",
        "errno",
        "provider_error_code",
        "connector_registration_count",
        "connector_disconnect_count",
        "connector_transport_failures",
        "connector_first_registered_ms",
        "category_min_gap",
        "subcategory_min_gap",
    }
)

_BOOLEAN_FIELDS = frozenset({"connector_registered"})


class JSONFormatter(logging.Formatter):
    """Serialize the event schema, never messages, exceptions or arbitrary objects."""

    def format(self, record: logging.LogRecord) -> str:
        event = record.__dict__.get("event")
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": event
            if isinstance(event, str) and _EVENT.fullmatch(event)
            else "log_record",
        }
        for field in sorted(_TEXT_FIELDS | _INTEGER_FIELDS | _BOOLEAN_FIELDS):
            if field not in record.__dict__:
                continue
            value = record.__dict__[field]
            if value is None:
                payload[field] = None
            elif field in _BOOLEAN_FIELDS and type(value) is bool:
                payload[field] = value
            elif field in _INTEGER_FIELDS and type(value) is int:
                payload[field] = value
            elif (
                field in _TEXT_FIELDS and isinstance(value, str) and len(value) <= 1024
            ):
                payload[field] = value
        return json.dumps(
            payload, ensure_ascii=True, separators=(",", ":"), allow_nan=False
        )


def configure_logging(level: int | str = logging.INFO) -> None:
    """Configure the application namespace on stderr; preserve unrelated handlers."""
    application = logging.getLogger("ask_every_day")
    application.setLevel(level)
    application.propagate = False
    for handler in application.handlers:
        if handler.name == _HANDLER_NAME:
            break
    else:
        handler = logging.StreamHandler(sys.stderr)
        handler.set_name(_HANDLER_NAME)
        application.addHandler(handler)
    if isinstance(handler, logging.StreamHandler):
        handler.setStream(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(JSONFormatter())
