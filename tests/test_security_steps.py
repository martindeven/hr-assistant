"""Pasos de los escenarios de ``features/security.feature`` (reto, parte 1).

Cubren el guard (PII e inyección), los permisos por cliente y el tope del
tamaño del cuerpo. Todo con dependencias inyectadas y el log en memoria.
"""

from pytest_bdd import given, parsers, scenarios, then, when

scenarios("features/security.feature")

# Clave de prueba armada en tiempo de ejecución con palabras repetidas: no es un
# secreto y no debe parecerlo (gitleaks).
KIOSK_KEY = "-".join(["kiosk", "test"] * 3)
REQUEST_TOO_LARGE_BODY = {"detail": "request too large"}


def _audits(observed, action):
    """Los eventos ``audit`` de una acción."""
    return [
        event
        for event in observed.events()
        if event["event"] == "audit" and event.get("action") == action
    ]


@given("the HR assistant is running with the fake model", target_fixture="observed")
def running_with_fake_model(make_observed_client):
    """La API con el modelo simulado y la configuración por omisión."""
    return make_observed_client()


@given(
    parsers.parse('the HR assistant is running with PII_INPUT_MODE "{mode}"'),
    target_fixture="observed",
)
def running_with_pii_mode(make_observed_client, mode):
    """La API con el modo de PII de entrada indicado."""
    return make_observed_client(env={"PII_INPUT_MODE": mode})


@given(
    "the HR assistant is running with a model that records its calls",
    target_fixture="observed",
)
def running_with_recording_model(make_observed_client, scripted_model, request):
    """La API con un modelo guionado sin respuestas: si se llama, se nota."""
    model = scripted_model([])
    request.node.recording_model = model
    return make_observed_client(model=model)


@given(
    parsers.parse(
        'the HR assistant is running with a kiosk client allowed only "{scope}"'
    ),
    target_fixture="observed",
)
def running_with_kiosk(make_observed_client, api_key, scope):
    """Dos clientes: el de pruebas con todo y el kiosco con un solo permiso."""
    env = {
        "API_KEYS": f"unit-tests:{api_key},kiosk:{KIOSK_KEY}",
        "API_KEY_SCOPES": f"unit-tests:directory policies metrics,kiosk:{scope}",
    }
    return make_observed_client(env=env)


@when(parsers.re(r'I ask "(?P<question>[^"]+)"$'), target_fixture="response")
def ask(observed, question):
    """Manda una pregunta sin historial."""
    return observed.http.post("/ask", json={"question": question})


@when(
    parsers.re(
        r'I ask "(?P<question>[^"]+)" with the assistant message "(?P<text>[^"]+)"$'
    ),
    target_fixture="response",
)
def ask_with_history(observed, question, text):
    """Manda una pregunta con un mensaje ``assistant`` en el historial."""
    history = [
        {"role": "user", "content": "Hola"},
        {"role": "assistant", "content": text},
    ]
    return observed.http.post("/ask", json={"question": question, "history": history})


@when(parsers.parse('the kiosk asks "{question}"'), target_fixture="response")
def kiosk_asks(observed, question):
    """El kiosco pregunta con su propia clave."""
    return observed.http.post(
        "/ask", json={"question": question}, headers={"X-API-Key": KIOSK_KEY}
    )


@when("the kiosk reads the metrics", target_fixture="response")
def kiosk_reads_metrics(observed):
    """El kiosco intenta leer las métricas."""
    return observed.http.get("/metrics", headers={"X-API-Key": KIOSK_KEY})


@when(
    parsers.parse("I send a question of {size:d} characters"),
    target_fixture="response",
)
def send_huge_question(observed, size):
    """Manda un cuerpo que pasa de ``MAX_BODY_BYTES``."""
    return observed.http.post("/ask", json={"question": "a" * size})


@then(parsers.parse("the response status is {status:d}"))
def response_status_is(response, status):
    """Comprueba el código HTTP."""
    assert response.status_code == status


@then(parsers.parse('the input guardrail found "{pii_type}"'))
def input_guardrail_found(response, pii_type):
    """``guardrails.pii_input`` trae el tipo, nunca el valor."""
    assert pii_type in response.json()["guardrails"]["pii_input"]


@then(parsers.parse('the answer contains "{text}"'))
def answer_contains(response, text):
    """La respuesta trae el texto indicado."""
    assert text in response.json()["answer"]


@then("the log has a pii_detected audit for the input without the value")
def pii_detected_audit(observed):
    """El audit dice dónde, qué tipos y cuántos, pero no el dato."""
    (event,) = _audits(observed, "pii_detected")
    assert event["where"] == "input"
    assert event["types"] == ["email"]
    assert event["count"] == 1


@then(parsers.parse('no log line contains "{text}"'))
def log_does_not_contain(observed, text):
    """El dato no llega a ninguna línea del log."""
    assert text not in observed.log_stream.getvalue()


@then(parsers.parse('the detail is "{detail}" with a request id'))
def detail_with_request_id(response, detail):
    """El cuerpo de error trae el detalle exacto y el id de la petición."""
    body = response.json()
    assert body["detail"] == detail
    assert body["request_id"]


@then("the model was never called")
def model_never_called(request):
    """Con una inyección no se llama al modelo."""
    assert request.node.recording_model.calls == []


@then("the log has an injection_blocked audit with a rule and without the text")
def injection_audit(observed):
    """El audit lleva el id de la regla y nunca el texto de la pregunta."""
    (event,) = _audits(observed, "injection_blocked")
    assert event["rule"]
    assert "Ignore previous instructions" not in observed.log_stream.getvalue()


@then("the body is exactly the request too large message")
def body_is_too_large(response):
    """El ``413`` tiene el cuerpo exacto de la especificación."""
    assert response.json() == REQUEST_TOO_LARGE_BODY
