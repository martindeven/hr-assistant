"""Pruebas de ``app.tracing.Tracer`` sin tocar la red."""

import langsmith
from langsmith.run_helpers import get_current_run_tree

from app.tracing import Tracer


class RecordingClient:
    """Cliente falso de LangSmith: guarda las corridas en vez de mandarlas."""

    def __init__(self):
        self.calls = []

    def create_run(self, **run):
        """Guarda una corrida nueva.

        :param run: Datos de la corrida
        :type run: dict
        """
        self.calls.append(("create", run))

    def update_run(self, *args, **run):
        """Guarda la actualización de una corrida.

        :param args: Argumentos posicionales (el id de la corrida)
        :type args: tuple
        :param run: Datos de la corrida
        :type run: dict
        """
        self.calls.append(("update", args, run))


def test_disabled_tracer_leaves_functions_untouched_and_creates_no_client(
    monkeypatch,
):
    def refuse_client(*_args, **_kwargs):
        raise AssertionError("a disabled tracer must not create a LangSmith Client")

    monkeypatch.setattr(langsmith.Client, "__init__", refuse_client)

    def lookup(employee_id):
        return {"id": employee_id}

    tracer = Tracer(enabled=False)

    assert tracer.wrap(lookup, name="get_employee", run_type="tool") is lookup
    with tracer.run_context("demo-123"):
        assert lookup("EMP-0004") == {"id": "EMP-0004"}


def test_enabled_tracer_records_the_request_id_in_the_trace_metadata():
    client = RecordingClient()
    tracer = Tracer(enabled=True, client=client)
    seen = {}

    def lookup(employee_id):
        seen["run"] = get_current_run_tree()
        return {"id": employee_id}

    traced = tracer.wrap(lookup, name="get_employee", run_type="tool")
    with tracer.run_context("demo-123"):
        result = traced("EMP-0004")

    assert traced is not lookup
    assert result == {"id": "EMP-0004"}
    assert seen["run"].metadata["request_id"] == "demo-123"
    assert client.calls, "the run must go to the injected client"


def test_trace_metadata_names_the_client_that_asked():
    tracer = Tracer(enabled=True, client=RecordingClient())
    seen = {}

    def lookup(employee_id):
        seen["run"] = get_current_run_tree()
        return {"id": employee_id}

    traced = tracer.wrap(lookup, name="get_employee", run_type="tool")
    with tracer.run_context("demo-123", client="ops"):
        traced("EMP-0004")

    assert seen["run"].metadata["client"] == "ops"
    assert seen["run"].metadata["request_id"] == "demo-123"
