"""Las páginas HTML del admin renderizan con sesión válida (firma de Starlette >= 1.0)."""

from fastapi.testclient import TestClient

from gmail_inbox_bot.admin_logs import SESSION_COOKIE, _make_session_cookie
from gmail_inbox_bot.app import app


def _client(monkeypatch) -> TestClient:
    monkeypatch.setenv("LOGS_VIEWER_PASSWORD", "secret")
    monkeypatch.setenv("DISABLE_BOT", "1")
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, _make_session_cookie("secret"))
    return client


def test_dashboard_page_renders(monkeypatch):
    resp = _client(monkeypatch).get("/admin/dashboard")
    assert resp.status_code == 200
    assert 'id="dateFrom"' in resp.text
    assert 'id="jevCards"' in resp.text


def test_logs_page_renders(monkeypatch):
    resp = _client(monkeypatch).get("/admin/logs")
    assert resp.status_code == 200
    assert "<html" in resp.text.lower()


def test_login_con_caracteres_no_ascii_no_da_500(monkeypatch):
    import gmail_inbox_bot.admin_logs as admin_logs

    monkeypatch.setattr(admin_logs, "_failed_logins", admin_logs.deque())
    resp = _client(monkeypatch).post("/admin/logs", data={"password": "contraseña"})
    assert resp.status_code == 401


def test_login_se_bloquea_tras_demasiados_fallos(monkeypatch):
    import gmail_inbox_bot.admin_logs as admin_logs

    monkeypatch.setattr(admin_logs, "_failed_logins", admin_logs.deque())
    client = _client(monkeypatch)
    for _ in range(admin_logs._MAX_FAILED_LOGINS):
        assert client.post("/admin/logs", data={"password": "mal"}).status_code == 401

    # Ni siquiera la contraseña buena entra hasta que pase la ventana.
    assert client.post("/admin/logs", data={"password": "secret"}).status_code == 429
