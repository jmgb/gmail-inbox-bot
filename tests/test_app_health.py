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


# ------------------------------------------------------------------
# Heartbeat: un thread vivo no basta, tiene que estar haciendo polls
# ------------------------------------------------------------------


@pytest.fixture
def bot_vivo(monkeypatch):
    import gmail_inbox_bot.bot as bot_module

    monkeypatch.setattr(app_module, "_bot_thread", _FakeThread(True))
    monkeypatch.setattr(app_module, "_reminder_thread", _FakeThread(True))
    monkeypatch.setattr(app_module, "_stall_reported", False)
    monkeypatch.setattr(bot_module, "poll_interval_seconds", 600)
    monkeypatch.setattr(bot_module, "bot_started_at", app_module.time.time() - 60)
    return bot_module


def test_poll_reciente_es_ok(bot_vivo, monkeypatch):
    monkeypatch.setattr(bot_vivo, "last_successful_poll", app_module.time.time() - 900)

    assert _health()["threads"]["bot"] == "alive"


def test_503_si_el_thread_vive_pero_no_hay_poll_correcto_hace_tres_ciclos(bot_vivo, monkeypatch):
    """Un bucle colgado o un buzón que no se puede leer dejaban el contenedor "healthy"."""
    monkeypatch.setattr(bot_vivo, "last_successful_poll", app_module.time.time() - 3 * 600 - 200)
    avisos = []
    monkeypatch.setattr(app_module.log, "error", lambda *a, **k: avisos.append(a))

    for _ in range(3):
        with pytest.raises(HTTPException) as exc:
            _health()

    assert exc.value.status_code == 503
    assert exc.value.detail["threads"]["bot"] == "stalled"
    assert len(avisos) == 1  # un aviso por atasco, no uno por healthcheck


def test_recien_arrancado_sin_poll_aun_no_hay_atasco(bot_vivo, monkeypatch):
    monkeypatch.setattr(bot_vivo, "last_successful_poll", None)

    assert _health()["threads"]["bot"] == "alive"


def test_arrancado_hace_mucho_sin_ningun_poll_correcto_es_atasco(bot_vivo, monkeypatch):
    """Un token revocado desde el arranque: nunca hay un poll correcto."""
    monkeypatch.setattr(bot_vivo, "last_successful_poll", None)
    monkeypatch.setattr(bot_vivo, "bot_started_at", app_module.time.time() - 4 * 600)
    monkeypatch.setattr(app_module.log, "error", lambda *a, **k: None)

    with pytest.raises(HTTPException) as exc:
        _health()

    assert exc.value.detail["threads"]["bot"] == "stalled"
