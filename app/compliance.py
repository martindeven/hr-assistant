"""Reglas de compliance que se aplican al arrancar con ``ENVIRONMENT=production``.

Cada regla está en una función propia; la tabla de
``doc/compliance-checklist.md`` dice dónde está cada una y qué prueba la cubre.
Los mensajes nombran variables, clientes y largos, nunca el valor de una clave.
"""

from app.errors import ConfigError

PRODUCTION_ENVIRONMENT = "production"
FORBIDDEN_PRODUCTION_PROVIDER = "fake"
MIN_PRODUCTION_KEY_LENGTH = 16
FAILURE_SEPARATOR = "; "
DISABLED_PII_INPUT_MODE = "off"


def _model_rule(settings):
    """Prohíbe el modelo simulado en producción.

    :param settings: Configuración ya leída
    :type settings: app.config.Settings
    :return: Las fallas de la regla
    :rtype: list[str]
    """
    if settings.model_provider == FORBIDDEN_PRODUCTION_PROVIDER:
        return [f"MODEL_PROVIDER must not be '{FORBIDDEN_PRODUCTION_PROVIDER}'"]
    return []


def _api_keys_rule(settings):
    """Exige al menos un cliente y claves de 16 caracteres o más.

    :param settings: Configuración ya leída
    :type settings: app.config.Settings
    :return: Las fallas de la regla, con el cliente y el largo de cada clave
    :rtype: list[str]
    """
    if not settings.api_keys:
        return ["API_KEYS must not be empty"]
    return [
        f"API_KEYS key of client '{client}' is too short (length {len(key)}, "
        f"minimum {MIN_PRODUCTION_KEY_LENGTH})"
        for client, key in settings.api_keys.items()
        if len(key) < MIN_PRODUCTION_KEY_LENGTH
    ]


def _tracing_rule(settings):
    """Con trazas encendidas, exige ocultar entradas y salidas.

    :param settings: Configuración ya leída
    :type settings: app.config.Settings
    :return: Las fallas de la regla
    :rtype: list[str]
    """
    if not settings.tracing_enabled:
        return []
    failures = []
    if not settings.hide_inputs:
        failures.append("HIDE_INPUTS must be 'true' when tracing is enabled")
    if not settings.hide_outputs:
        failures.append("HIDE_OUTPUTS must be 'true' when tracing is enabled")
    return failures


def _pii_input_rule(settings):
    """Prohíbe apagar la revisión de datos personales de la entrada.

    :param settings: Configuración ya leída
    :type settings: app.config.Settings
    :return: Las fallas de la regla
    :rtype: list[str]
    """
    if settings.pii_input_mode == DISABLED_PII_INPUT_MODE:
        return [f"PII_INPUT_MODE must not be '{DISABLED_PII_INPUT_MODE}'"]
    return []


def _injection_guard_rule(settings):
    """Exige el bloqueo de inyección de instrucciones.

    :param settings: Configuración ya leída
    :type settings: app.config.Settings
    :return: Las fallas de la regla
    :rtype: list[str]
    """
    if not settings.injection_guard:
        return ["INJECTION_GUARD must be 'true'"]
    return []


def _scopes_rule(settings):
    """Exige una entrada de ``API_KEY_SCOPES`` para cada cliente de ``API_KEYS``.

    :param settings: Configuración ya leída
    :type settings: app.config.Settings
    :return: Las fallas de la regla, con el nombre de cada cliente (nunca su
        clave)
    :rtype: list[str]
    """
    return [
        f"API_KEY_SCOPES has no entry for client '{client}'"
        for client in settings.api_keys
        if client not in settings.api_key_scopes
    ]


RULES = (
    _model_rule,
    _api_keys_rule,
    _tracing_rule,
    _pii_input_rule,
    _injection_guard_rule,
    _scopes_rule,
)


def check_production(settings):
    """Revisa las reglas de producción y junta todas las fallas en un error.

    Fuera de producción no hace nada.

    :param settings: Configuración ya leída
    :type settings: app.config.Settings
    :raises app.errors.ConfigError: Si alguna regla falla en producción
    """
    if settings.environment != PRODUCTION_ENVIRONMENT:
        return
    failures = [failure for rule in RULES for failure in rule(settings)]
    if failures:
        raise ConfigError(
            "production compliance failed: " + FAILURE_SEPARATOR.join(failures)
        )
