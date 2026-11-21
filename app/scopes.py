"""Permisos por cliente (``API_KEY_SCOPES``, punto 3.4 de la especificación).

Forma: ``cliente:permiso permiso,cliente2:permiso``. Los permisos son
``directory``, ``policies`` y ``metrics``. Los errores nombran la variable, el
número de entrada y, si está en ``API_KEYS``, el cliente; nunca repiten un
cliente ajeno, un permiso desconocido ni el texto de la entrada (alguien podría
pegar ahí una clave por error) y se lanzan sin encadenar otra excepción.
"""

from app.errors import ConfigError

SCOPES_VARIABLE = "API_KEY_SCOPES"
ENTRY_SEPARATOR = ","
CLIENT_SEPARATOR = ":"
DIRECTORY_PERMISSION = "directory"
POLICIES_PERMISSION = "policies"
METRICS_PERMISSION = "metrics"
PERMISSIONS = frozenset(
    {DIRECTORY_PERMISSION, POLICIES_PERMISSION, METRICS_PERMISSION}
)
NO_PERMISSIONS = frozenset()
DEVELOPMENT_ENVIRONMENT = "development"


def _error(number, problem):
    """Crea el error de una entrada sin mostrar su texto.

    :param number: Número de la entrada, desde 1
    :type number: int
    :param problem: Qué está mal (sin valores de la entrada)
    :type problem: str
    :return: El error listo para lanzar
    :rtype: app.errors.ConfigError
    """
    return ConfigError(f"{SCOPES_VARIABLE} entry {number} {problem}")


def _parse_entry(number, entry, clients):
    """Lee una entrada ``cliente:permiso permiso``.

    :param number: Número de la entrada, desde 1
    :type number: int
    :param entry: Entrada sin espacios alrededor
    :type entry: str
    :param clients: Clientes de ``API_KEYS``
    :type clients: collections.abc.Collection[str]
    :return: El cliente y sus permisos
    :rtype: tuple[str, frozenset[str]]
    :raises app.errors.ConfigError: Si la entrada está mal formada
    """
    client, separator, raw_permissions = entry.partition(CLIENT_SEPARATOR)
    client = client.strip()
    if not entry or not separator or not client:
        raise _error(number, "must have the form client:permission")
    # Un cliente ajeno no se repite en el mensaje: podría ser una clave pegada
    # por error. Solo se nombran los clientes que sí están en ``API_KEYS``.
    if client not in clients:
        raise _error(number, "names a client that is not in API_KEYS")
    permissions = raw_permissions.split()
    if not permissions:
        raise _error(number, f"has no permissions for client '{client}'")
    unknown = [name for name in permissions if name not in PERMISSIONS]
    if unknown:
        allowed = ", ".join(sorted(PERMISSIONS))
        raise _error(
            number,
            f"has {len(unknown)} unknown permission(s) for client '{client}' "
            f"(allowed: {allowed})",
        )
    return client, frozenset(permissions)


def parse_api_key_scopes(raw, clients):
    """Lee ``API_KEY_SCOPES``.

    Vacía o con solo espacios da un diccionario vacío.

    :param raw: Valor de ``API_KEY_SCOPES``
    :type raw: str or None
    :param clients: Clientes de ``API_KEYS`` (no se miran sus claves)
    :type clients: collections.abc.Collection[str]
    :return: Los permisos de cada cliente
    :rtype: dict[str, frozenset[str]]
    :raises app.errors.ConfigError: Si una entrada está vacía, no tiene ``:``,
        no tiene permisos, nombra un permiso desconocido, repite un cliente o
        nombra uno que no está en ``API_KEYS``
    """
    text = (raw or "").strip()
    if not text:
        return {}
    known = frozenset(clients)
    scopes = {}
    for number, piece in enumerate(text.split(ENTRY_SEPARATOR), start=1):
        client, permissions = _parse_entry(number, piece.strip(), known)
        if client in scopes:
            raise _error(number, f"repeats client '{client}'")
        scopes[client] = permissions
    return scopes


def permissions_for(client, scopes, environment):
    """Permisos de un cliente: el único lugar que los decide.

    En ``development``, un cliente que no aparece en ``API_KEY_SCOPES`` tiene
    todos los permisos (para no romper lo anterior); en ``production`` no tiene
    ninguno.

    :param client: Nombre del cliente, o ``None``
    :type client: str or None
    :param scopes: Permisos leídos de ``API_KEY_SCOPES``
    :type scopes: dict[str, frozenset[str]]
    :param environment: ``development`` o ``production``
    :type environment: str
    :return: Los permisos del cliente
    :rtype: frozenset[str]
    """
    if client is not None and client in scopes:
        return frozenset(scopes[client])
    if environment == DEVELOPMENT_ENVIRONMENT:
        return PERMISSIONS
    return NO_PERMISSIONS
