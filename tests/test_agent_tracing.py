"""Trazas por agente y pasos medidos (reto, parte 2, puntos 1 y 2).

Las trazas van a un cliente de LangSmith falso que guarda las corridas; ninguna
prueba toca la red.
"""

import io
import json

import langsmith
import pytest
from fastapi.testclient import TestClient

from app.agent_steps import StepRecorder
from app.config import load_settings
from app.guard import GuardBlocked
from app.llm import FakeModel, ModelUnavailableError
from app.main import create_app
from app.pipeline import Forbidden
from app.tracing import Tracer

QUESTION = "¿Quién es EMP-0004?"
PROJECT = "hr-tests"
ROOT_METADATA = (
    "request_id",
    "client",
    "environment",
    "model",
    "route",
    "agents",
    "cost_usd",
    "cache",
    "fallback",
)


class RecordingClient:
    """Cliente falso de LangSmith: guarda las corridas en vez de mandarlas."""

    def __init__(self):
        self.calls = []

    def create_run(self, **run):
        """Guarda una corrida nueva.

        :param run: Datos de la corrida
        :type run: dict
        """
        self.calls.append(("create", (), run))

    def update_run(self, *args, **run):
        """Guarda la actualización de una corrida.

        :param args: Argumentos posicionales (el id de la corrida)
        :type args: tuple
        :param run: Datos de la corrida
        :type run: dict
        """
        self.calls.append(("update", args, run))

    def runs(self):
        """Junta la creación y las actualizaciones de cada corrida.

        :return: El último estado conocido de cada corrida
        :rtype: list[dict]
        """
        merged = {}
        for kind, args, data in self.calls:
            if kind == "create":
                run_id = str(data["id"])
            else:
                run_id = str(data.get("run_id") or args[0])
            run = merged.setdefault(run_id, {"id": run_id})
            known = {key: value for key, value in data.items() if value is not None}
            run.update(known)
        return list(merged.values())


def metadata(run):
    """Los metadatos de una corrida."""
    return (run.get("extra") or {}).get("metadata") or {}


def traced_answer(directory, keyed_env, api_key):
    """Hace una pregunta con las trazas encendidas hacia el cliente falso.

    :return: El cliente falso y el cuerpo de la respuesta
    :rtype: tuple[RecordingClient, dict]
    """
    recording = RecordingClient()
    app = create_app(
        settings=load_settings(keyed_env()),
        model=FakeModel(directory.departments()),
        directory=directory,
        log_stream=io.StringIO(),
        tracer=Tracer(enabled=True, client=recording, project_name=PROJECT),
    )
    http = TestClient(app, headers={"X-API-Key": api_key})
    response = http.post("/ask", json={"question": QUESTION})
    assert response.status_code == 200
    return recording, response.json()


def test_the_ask_trace_root_carries_the_request_metadata_and_tags(
    directory, keyed_env, api_key
):
    recording, body = traced_answer(directory, keyed_env, api_key)

    runs = recording.runs()
    roots = [run for run in runs if run["name"] == "ask"]
    assert len(roots) == 1
    root = roots[0]
    assert root["run_type"] == "chain"
    assert not root.get("parent_run_id")
    assert root["session_name"] == PROJECT
    found = metadata(root)
    assert set(ROOT_METADATA) <= set(found)
    assert found["request_id"] == body["request_id"]
    assert found["client"] == "unit-tests"
    assert found["environment"] == "development"
    assert found["agents"] == body["agents"]
    assert found["cache"] == "none" and found["fallback"] is False
    assert {"development", *body["agents"]} <= set(root["tags"])
    sent = json.dumps(
        [[metadata(run), run.get("tags")] for run in runs], default=str
    )
    assert "Quién es" not in sent


def test_each_agent_is_a_child_node_and_the_specialist_holds_llm_and_tool_nodes(
    directory, keyed_env, api_key
):
    recording, body = traced_answer(directory, keyed_env, api_key)

    runs = recording.runs()
    root = next(run for run in runs if run["name"] == "ask")
    agents = {
        run["name"]: run
        for run in runs
        if str(run.get("parent_run_id")) == root["id"]
    }
    assert set(body["agents"]) <= set(agents)
    for name in body["agents"]:
        assert metadata(agents[name])["agent"] == name
        assert metadata(agents[name])["outcome"] == "ok"
        assert name in agents[name]["tags"]
    inside_directory = {
        run["run_type"]
        for run in runs
        if str(run.get("parent_run_id")) == agents["directory"]["id"]
    }
    assert inside_directory == {"llm", "tool"}


def test_a_disabled_tracer_never_touches_langsmith(
    monkeypatch, make_observed_client
):
    def refuse_client(*_args, **_kwargs):
        raise AssertionError("a disabled tracer must not create a LangSmith Client")

    monkeypatch.setattr(langsmith.Client, "__init__", refuse_client)
    tracer = Tracer(enabled=False)

    with tracer.request_span("demo-123", "unit-tests", "development") as root:
        root.annotate(model="fake", cost_usd=0.0)
        with tracer.agent_span("guard") as node:
            node.annotate(outcome="ok")
    response = make_observed_client().http.post("/ask", json={"question": QUESTION})

    assert response.status_code == 200


def test_each_failure_becomes_its_outcome_and_is_raised_again(fake_clock):
    failures = [
        (GuardBlocked("request blocked by policy"), "blocked"),
        (Forbidden(), "forbidden"),
        (ModelUnavailableError("gateway returned status 503"), "error"),
        (RuntimeError("unexpected"), "error"),
    ]
    for error, outcome in failures:
        recorder = StepRecorder(fake_clock(), Tracer(enabled=False), "development")

        with pytest.raises(type(error)):
            with recorder.step("worker"):
                raise error

        assert [step.outcome for step in recorder.steps] == [outcome], outcome


def test_a_step_measures_its_latency_and_a_skipped_agent_is_recorded(fake_clock):
    recorder = StepRecorder(
        fake_clock(step_seconds=0.25), Tracer(enabled=False), "development"
    )

    with recorder.step("guard"):
        pass
    recorder.skip("reviewer")

    guard, reviewer = recorder.steps
    assert (guard.agent, guard.outcome) == ("guard", "ok")
    assert guard.latency_ms == pytest.approx(250)
    assert (reviewer.agent, reviewer.outcome) == ("reviewer", "skipped")
