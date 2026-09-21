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
