"""Pruebas de ``app.logs.JsonLogger``: una línea JSON por evento."""

import io
import json
from datetime import datetime, timedelta

from app.logs import JsonLogger


def test_each_event_is_one_json_line_with_the_base_fields(fake_clock):
    stream = io.StringIO()
    logger = JsonLogger(stream=stream, clock=fake_clock(), service="hr-assistant")

    logger.emit("audit", "demo-123", action="tool_call", tool="get_employee")
    logger.emit("agent_run", "demo-123", department="Tecnología")

    lines = stream.getvalue().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["service"] == "hr-assistant"
    assert first["event"] == "audit"
    assert first["request_id"] == "demo-123"
    assert first["tool"] == "get_employee"
    # ``ts`` es ISO 8601 y está en UTC.
    assert datetime.fromisoformat(first["ts"]).utcoffset() == timedelta(0)
    # Los acentos se escriben tal cual, sin escapar.
    assert "Tecnología" in lines[1]
