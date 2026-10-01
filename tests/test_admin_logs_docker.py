"""El endpoint del Docker Engine API es configurable: socket unix o socket-proxy TCP.

Montar /var/run/docker.sock en el contenedor concede leer y enumerar *todos* los
contenedores del VPS, cuando el visor solo lee el suyo (`DOCKER_CONTAINERS`). Con
`DOCKER_API_TCP` se habla con un socket-proxy que restringe la ruta por allowlist.
Sin la variable, el comportamiento es el de siempre.
"""

import socket as socket_module

import pytest

import gmail_inbox_bot.admin_logs as admin_logs


class _FakeConn:
    def __init__(self, host, timeout=None):
        self.host = host
        self.timeout = timeout
        self.sock = None


@pytest.fixture
def conexiones(monkeypatch):
    creadas: list[_FakeConn] = []
    unix_conectados: list[str] = []

    def fake_conn(host, timeout=None):
        conn = _FakeConn(host, timeout=timeout)
        creadas.append(conn)
        return conn

    class _FakeUnixSocket:
        def __init__(self, *args, **kwargs):
            pass

        def settimeout(self, _timeout):
            pass

        def connect(self, path):
            unix_conectados.append(path)

    monkeypatch.setattr(admin_logs.http.client, "HTTPConnection", fake_conn)
    monkeypatch.setattr(admin_logs.socket, "socket", _FakeUnixSocket)
    return creadas, unix_conectados


def test_sin_variable_usa_el_socket_unix(monkeypatch, conexiones):
    creadas, unix_conectados = conexiones
    monkeypatch.delenv("DOCKER_API_TCP", raising=False)

    admin_logs._docker_connection(timeout=15)

    assert unix_conectados == [admin_logs.DOCKER_SOCKET]
    assert creadas[0].host == "localhost"


def test_con_variable_usa_tcp_y_no_toca_el_socket_unix(monkeypatch, conexiones):
    creadas, unix_conectados = conexiones
    monkeypatch.setenv("DOCKER_API_TCP", "gmail-docker-socket-proxy:2375")

    conn = admin_logs._docker_connection(timeout=15)

    assert unix_conectados == []
    assert creadas[0].host == "gmail-docker-socket-proxy:2375"
    assert conn.sock is None


def test_variable_vacia_equivale_a_no_definida(monkeypatch, conexiones):
    _creadas, unix_conectados = conexiones
    monkeypatch.setenv("DOCKER_API_TCP", "   ")

    admin_logs._docker_connection(timeout=15)

    assert unix_conectados == [admin_logs.DOCKER_SOCKET]


def test_el_modulo_sigue_importando_socket_unix_de_verdad():
    """Guarda contra un refactor que deje el fixture mintiendo."""
    assert admin_logs.socket is socket_module


def test_estado_del_contenedor_por_la_api_y_no_por_el_cli(monkeypatch):
    """La imagen no trae el CLI de docker: el estado se lee de /containers/<c>/json."""
    import asyncio

    pedidos = []

    def fake_get(path, timeout=15):
        pedidos.append(path)
        return b'{"State": {"Status": "running"}}'

    monkeypatch.setattr(admin_logs, "_docker_api_get", fake_get)

    assert asyncio.run(admin_logs._docker_container_status("gmail-inbox-bot")) == "running"
    assert pedidos == ["/containers/gmail-inbox-bot/json"]


def test_contenedor_inexistente_es_not_found(monkeypatch):
    import asyncio

    def fake_get(path, timeout=15):
        raise RuntimeError("Docker API 404: no such container")

    monkeypatch.setattr(admin_logs, "_docker_api_get", fake_get)

    assert asyncio.run(admin_logs._docker_container_status("x")) == "not_found"
