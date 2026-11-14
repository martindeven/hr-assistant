"""Errores compartidos por la configuración, la seguridad y el compliance.

Vive aparte para que ``app.config`` y ``app.security`` no se importen en
círculo; ``app.config`` lo vuelve a exportar.
"""


class ConfigError(ValueError):
    """Error de configuración: una variable de entorno tiene un valor inválido."""
