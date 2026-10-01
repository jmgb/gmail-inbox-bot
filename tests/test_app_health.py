"""/health refleja si los threads daemon siguen vivos.

El bot y el scheduler corren como daemon threads dentro del proceso de FastAPI. Si
uno muere, ``_run_bot_in_thread`` lo loguea y el thread termina, pero el proceso sigue
en pie y el servidor sigue contestando 200: el contenedor queda "Up" y nadie se entera
de que no se está clasificando nada. El healthcheck necesita verlo.
"""

import asyncio

import pytest
from fastapi import HTTPException

import gmail_inbox_bot.app as app_module


class _FakeThread:
    def __init__(self, alive: bool) -> None:
        self._alive = alive

    def is_alive(self) -> bool:
        return self._alive


@pytest.fixture(autouse=True)
def _bot_enabled(monkeypatch):
    monkeypatch.delenv("DISABLE_BOT", raising=False)


def _health() -> dict:
    return asyncio.run(app_module.health())


def test_ok_cuando_los_dos_threads_viven(monkeypatch):
    monkeypatch.setattr(app_module, "_bot_thread", _FakeThread(True))
    monkeypatch.setattr(app_module, "_reminder_thread", _FakeThread(True))

    payload = _health()

    assert payload["status"] == "ok"
    assert payload["threads"] == {"bot": "alive", "calendar_reminders": "alive"}


def test_503_cuando_el_thread_del_bot_ha_muerto(monkeypatch):
    monkeypatch.setattr(app_module, "_bot_thread", _FakeThread(False))
    monkeypatch.setattr(app_module, "_reminder_thread", _FakeThread(True))

    with pytest.raises(HTTPException) as exc:
        _health()

    assert exc.value.status_code == 503
    assert exc.value.detail["threads"]["bot"] == "dead"


def test_503_cuando_el_scheduler_ha_muerto(monkeypatch):
    monkeypatch.setattr(app_module, "_bot_thread", _FakeThread(True))
    monkeypatch.setattr(app_module, "_reminder_thread", _FakeThread(False))

    with pytest.raises(HTTPException) as exc:
        _health()

    assert exc.value.status_code == 503
    assert exc.value.detail["threads"]["calendar_reminders"] == "dead"


def test_ok_en_modo_solo_admin(monkeypatch):
    """Con DISABLE_BOT no hay threads a propósito: eso no es una degradación."""
    monkeypatch.setenv("DISABLE_BOT", "1")
    monkeypatch.setattr(app_module, "_bot_thread", None)
    monkeypatch.setattr(app_module, "_reminder_thread", None)

    payload = _health()

    assert payload["status"] == "ok"
    assert payload["threads"] == {"bot": "disabled", "calendar_reminders": "disabled"}


def test_el_arranque_de_la_app_lanza_los_dos_threads(monkeypatch):
    """Tras migrar de on_event (deprecado) a lifespan, el arranque sigue lanzando el trabajo."""
    import threading

    from fastapi.testclient import TestClient

    arrancados = []
    hecho = threading.Event()

    def registrar(nombre):
        def run():
            arrancados.append(nombre)
            if len(arrancados) == 2:
                hecho.set()

        return run

    monkeypatch.setattr(app_module, "_run_bot_in_thread", registrar("bot"))
    monkeypatch.setattr(app_module, "_run_reminder_scheduler", registrar("calendar"))
    sleep_original = asyncio.sleep
    monkeypatch.setattr(app_module.asyncio, "sleep", lambda _s: sleep_original(0))

    with TestClient(app_module.app):
        assert hecho.wait(5)

    assert sorted(arrancados) == ["bot", "calendar"]
