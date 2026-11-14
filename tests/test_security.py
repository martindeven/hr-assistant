"""Pruebas de ``app.security``: claves por cliente y rutas protegidas.

El autenticador debe llamar a ``hmac.compare_digest`` a través del módulo
``hmac`` (no con ``from hmac import ...``) para que la prueba pueda espiarlo.
"""

import hmac

import pytest

from app.config import ConfigError
from app.security import ApiKeyAuthenticator, parse_api_keys

INVALID_KEY_BODY = {"detail": "invalid or missing API key"}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ops:key-one", {"ops": "key-one"}),
        (" ops:key-one , web:key-two ", {"ops": "key-one", "web": "key-two"}),
        ("ops:key:with:colons", {"ops": "key:with:colons"}),
        ("", {}),
        ("   ", {}),
    ],
    ids=["one_client", "two_clients_with_spaces", "key_with_colons", "empty", "blank"],
)
def test_api_keys_are_parsed_into_clients(raw, expected):
    assert parse_api_keys(raw) == expected


@pytest.mark.parametrize(
    ("raw", "entry", "length", "secret"),
    [
        ("no-colon-secret", 1, 15, "no-colon-secret"),
        ("a:k1,", 2, 0, None),
        ("a:k1,:orphan-secret", 2, 14, "orphan-secret"),
        ("lonely-client:", 1, 14, "lonely-client"),
        ("a:k1,a:k2-secret", 2, 11, "k2-secret"),
        ("a:same-secret,b:same-secret", 2, 13, "same-secret"),
    ],
    ids=[
        "missing_colon",
        "trailing_comma",
        "empty_client",
        "empty_key",
        "repeated_client",
        "repeated_key",
    ],
)
def test_malformed_api_keys_name_the_entry_and_length_but_never_the_value(
    raw, entry, length, secret
):
    with pytest.raises(ConfigError) as error:
        parse_api_keys(raw)

    message = str(error.value)
    assert f"entry {entry}" in message
    assert f"length {length}" in message
    # ``from None``: el error no encadena otra excepción que traiga el valor.
    assert error.value.__cause__ is None
    if secret:
        assert secret not in message
        context = error.value.__context__
        assert context is None or secret not in str(context)


def test_authenticator_returns_the_client_only_for_a_matching_key():
    authenticator = ApiKeyAuthenticator({"ops": "key-one", "web": "key-two"})

    assert authenticator.identify("key-two") == "web"
    assert authenticator.identify("wrong-key") is None
    assert authenticator.identify("") is None
    assert authenticator.identify(None) is None


def test_authenticator_compares_every_key_with_compare_digest(monkeypatch):
    original = hmac.compare_digest
    calls = []

    def spy(first, second):
        calls.append((first, second))
        return original(first, second)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    authenticator = ApiKeyAuthenticator({"ops": "key-one", "web": "key-two"})

    assert authenticator.identify("key-one") == "ops"
    # Sin salida temprana: se compara con las dos claves aunque la primera sirva.
    assert len(calls) == 2


def test_unknown_route_without_a_key_is_rejected_instead_of_not_found(
    make_observed_client,
):
    observed = make_observed_client(send_key=False)

    response = observed.http.get("/admin")

    assert response.status_code == 401
    assert response.json() == INVALID_KEY_BODY


def test_missing_key_is_reported_before_an_invalid_body(make_observed_client):
    observed = make_observed_client(send_key=False)

    response = observed.http.post("/ask", json={})

    assert response.status_code == 401
    assert response.json() == INVALID_KEY_BODY
