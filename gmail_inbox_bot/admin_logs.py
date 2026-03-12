"""Admin Logs Viewer Router.

Web-based log viewing for Docker containers and application logs.
Protected with a simple password via LOGS_VIEWER_PASSWORD env var.
When no password is configured, all endpoints return 404 (feature hidden).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.client
import os
import secrets
import socket
import time
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    StreamingResponse,
)
from fastapi.templating import Jinja2Templates

from .logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-logs"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ---------------------------------------------------------------------------
# Log source definitions
# ---------------------------------------------------------------------------

LOGS_DIR = Path("/app/logs")

DOCKER_CONTAINERS: dict[str, str] = {
    "docker_gmail": "gmail-inbox-bot",
}

FILE_LOGS: dict[str, str] = {
    "app": "app.log",
}

SYSTEM_LOGS: dict[str, str] = {}

LOG_LABELS: dict[str, str] = {
    "docker_gmail": "Docker: gmail-inbox-bot",
    "app": "App Log",
}

ALL_LOG_NAMES = set(DOCKER_CONTAINERS) | set(FILE_LOGS) | set(SYSTEM_LOGS)

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

SESSION_COOKIE = "gmail_bot_logs_session"
SESSION_TTL = 12 * 60 * 60  # 12 hours


def _get_password() -> str:
    return os.getenv("LOGS_VIEWER_PASSWORD", "")


def _make_session_cookie(password: str) -> str:
    """Create an HMAC-SHA256 signed session cookie value with TTL."""
    expires = str(int(time.time()) + SESSION_TTL)
    payload = f"gmail_bot_logs|{expires}"
    sig = hmac.new(password.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}|{sig}"


def _validate_session_cookie(value: str, password: str) -> bool:
    """Validate an HMAC-SHA256 signed session cookie."""
    parts = value.split("|")
    if len(parts) != 3:
        return False
    prefix, expires_str, sig = parts
    if prefix != "gmail_bot_logs":
        return False
    try:
        expires = int(expires_str)
    except ValueError:
        return False
    if time.time() > expires:
        return False
    expected_payload = f"{prefix}|{expires_str}"
    expected_sig = hmac.new(
        password.encode(), expected_payload.encode(), hashlib.sha256
    ).hexdigest()
    return secrets.compare_digest(sig, expected_sig)


def _require_logs_password(request: Request) -> bool:
    """Check if the request has valid logs auth via cookie."""
    password = _get_password()
    if not password:
        return False

    cookie = request.cookies.get(SESSION_COOKIE, "")
    return bool(cookie and _validate_session_cookie(cookie, password))


def _is_development() -> bool:
    return os.getenv("ENVIRONMENT", "production").lower() in ("development", "dev")


# ---------------------------------------------------------------------------
# Log reading helpers
# ---------------------------------------------------------------------------

DOCKER_SOCKET = "/var/run/docker.sock"


def _docker_api_get(path: str, timeout: float = 15) -> bytes:
    """Synchronous GET to the Docker Engine API via Unix socket."""
    conn = http.client.HTTPConnection("localhost", timeout=timeout)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(DOCKER_SOCKET)
    conn.sock = sock
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        if resp.status != 200:
            body = resp.read().decode(errors="replace")
            raise RuntimeError(f"Docker API {resp.status}: {body[:500]}")
        return resp.read()
    finally:
        conn.close()


async def _read_docker_logs(container: str, lines: int = 500) -> str:
    """Read last N lines from a Docker container via Docker Engine API socket."""
    try:
        path = (
            f"/containers/{quote(container, safe='')}/logs"
            f"?stdout=1&stderr=1&tail={lines}&timestamps=1"
        )
        raw = await asyncio.get_event_loop().run_in_executor(None, _docker_api_get, path)
        decoded_lines: list[str] = []
        offset = 0
        while offset + 8 <= len(raw):
            frame_size = int.from_bytes(raw[offset + 4 : offset + 8], "big")
            if offset + 8 + frame_size > len(raw):
                break
            frame = raw[offset + 8 : offset + 8 + frame_size]
            decoded_lines.append(frame.decode(errors="replace"))
            offset += 8 + frame_size
        if not decoded_lines and raw:
            return raw.decode(errors="replace")
        return "".join(decoded_lines)
    except FileNotFoundError:
        return f"Docker socket not found at {DOCKER_SOCKET}\n"
    except PermissionError:
        return f"Permission denied accessing {DOCKER_SOCKET}. Check group_add in docker-compose.\n"
    except Exception as exc:
        logger.error("_read_docker_logs(%s): %s", container, exc)
        return f"Error reading docker logs: {exc}\n"


def _resolve_file_path(log_name: str) -> str | None:
    """Return absolute path for a filesystem-based log, or None."""
    if log_name in FILE_LOGS:
        return str(LOGS_DIR / FILE_LOGS[log_name])
    if log_name in SYSTEM_LOGS:
        return SYSTEM_LOGS[log_name]
    return None


async def _read_file_logs(file_path: str, lines: int = 500) -> str:
    """Read last N lines from a log file on disk."""
    try:
        if not os.path.exists(file_path):
            return f"Log file not found: {file_path}\n"
        with open(file_path, encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            return "".join(last_lines)
    except PermissionError:
        return f"Permission denied: {file_path}\n"
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Log file access error for %s: %s", file_path, exc)
        return f"Error reading log file: {exc}\n"
    except Exception as exc:
        logger.error("Unexpected error reading log %s: %s", file_path, exc)
        return f"Error reading log file: {exc}\n"


async def _get_log_content(log_name: str, lines: int = 500) -> str:
    """Unified reader: docker or file log."""
    if log_name in DOCKER_CONTAINERS:
        return await _read_docker_logs(DOCKER_CONTAINERS[log_name], lines)
    file_path = _resolve_file_path(log_name)
    if file_path:
        return await _read_file_logs(file_path, lines)
    return f"Unknown log source: {log_name}\n"


# ---------------------------------------------------------------------------
# Login form HTML (inline to avoid a second template)
# ---------------------------------------------------------------------------

LOGIN_HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="robots" content="noindex, nofollow">
<title>Gmail Bot - Log Viewer Login</title>
<style>
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #1e1e1e; color: #d4d4d4; display: flex; justify-content: center;
       align-items: center; height: 100vh; margin: 0; }
.login-box { background: #2d2d30; padding: 40px; border-radius: 8px;
             border: 1px solid #3e3e42; text-align: center; max-width: 360px; width: 90%; }
h1 { color: #4ec9b0; font-size: 20px; margin-bottom: 24px; }
input { background: #3c3c3c; color: #d4d4d4; border: 1px solid #3e3e42; padding: 10px 14px;
        border-radius: 4px; font-size: 14px; width: 100%; box-sizing: border-box;
        margin-bottom: 16px; outline: none; }
input:focus { border-color: #0e639c; }
button { background: #0e639c; color: white; border: none; padding: 10px 24px;
         border-radius: 4px; cursor: pointer; font-size: 14px; width: 100%; }
button:hover { background: #1177bb; }
.error { color: #f48771; font-size: 13px; margin-bottom: 12px; }
</style>
</head>
<body>
<div class="login-box">
  <h1>Gmail Bot - Log Viewer</h1>
  {error}
  <form method="post" action="/admin/logs">
    <input type="password" name="password" placeholder="Password" autofocus required>
    <button type="submit">Acceder</button>
  </form>
</div>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/logs", response_class=HTMLResponse, response_model=None)
async def logs_viewer_login(password: str = Form(...)) -> HTMLResponse | RedirectResponse:
    """Handle login form submission via POST."""
    expected_password = _get_password()
    if not expected_password:
        raise HTTPException(status_code=404)

    if secrets.compare_digest(password, expected_password):
        response = RedirectResponse(url="/admin/dashboard", status_code=302)
        cookie_value = _make_session_cookie(expected_password)
        response.set_cookie(
            SESSION_COOKIE,
            cookie_value,
            max_age=SESSION_TTL,
            httponly=True,
            secure=not _is_development(),
            samesite="strict",
        )
        return response

    return HTMLResponse(
        LOGIN_HTML.replace("{error}", '<p class="error">Password incorrecto</p>'),
        status_code=401,
    )


@router.get("/logs", response_class=HTMLResponse, response_model=None)
async def logs_viewer_ui(request: Request) -> HTMLResponse:
    """Web UI for viewing logs. Shows login form or full viewer (requires valid cookie)."""
    password = _get_password()
    if not password:
        raise HTTPException(status_code=404)

    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie and _validate_session_cookie(cookie, password):
        resp = templates.TemplateResponse(
            "admin_logs.html",
            {
                "request": request,
                "log_labels": LOG_LABELS,
                "docker_keys": list(DOCKER_CONTAINERS.keys()),
                "file_keys": list(FILE_LOGS.keys()),
                "system_keys": list(SYSTEM_LOGS.keys()),
            },
        )
        return resp

    return HTMLResponse(LOGIN_HTML.replace("{error}", ""), status_code=200)


@router.get("/logs/tail", response_class=PlainTextResponse)
async def tail_logs(
    request: Request,
    log_name: str = Query("app"),
    lines: int = Query(500, ge=10, le=10000),
) -> PlainTextResponse:
    """Get last N lines from specified log source."""
    if not _require_logs_password(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if log_name not in ALL_LOG_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown log source: {log_name}")
    content = await _get_log_content(log_name, lines)
    return PlainTextResponse(content=content)


@router.get("/logs/list")
async def list_logs(request: Request) -> dict[str, Any]:
    """List all available log sources with metadata."""
    if not _require_logs_password(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    result: dict[str, Any] = {}

    for name, container in DOCKER_CONTAINERS.items():
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "--format",
                "{{.State.Status}}",
                container,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            status = stdout.decode().strip() if proc.returncode == 0 else "not_found"
        except Exception:
            status = "unknown"
        result[name] = {
            "type": "docker",
            "container": container,
            "label": LOG_LABELS.get(name, name),
            "status": status,
        }

    for name, filename in FILE_LOGS.items():
        path = LOGS_DIR / filename
        if path.exists():
            stat = path.stat()
            result[name] = {
                "type": "file",
                "path": str(path),
                "label": LOG_LABELS.get(name, name),
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "exists": True,
            }
        else:
            result[name] = {
                "type": "file",
                "path": str(path),
                "label": LOG_LABELS.get(name, name),
                "exists": False,
            }

    for name, path_str in SYSTEM_LOGS.items():
        path = Path(path_str)
        if path.exists():
            stat = path.stat()
            result[name] = {
                "type": "system",
                "path": path_str,
                "label": LOG_LABELS.get(name, name),
                "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / (1024 * 1024), 2),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                "exists": True,
            }
        else:
            result[name] = {
                "type": "system",
                "path": path_str,
                "label": LOG_LABELS.get(name, name),
                "exists": False,
            }

    return result


@router.get("/logs/download/{log_name}")
async def download_log(request: Request, log_name: str) -> StreamingResponse:
    """Download a complete log file or docker log dump."""
    if not _require_logs_password(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    if log_name not in ALL_LOG_NAMES:
        raise HTTPException(status_code=404, detail=f"Unknown log source: {log_name}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{log_name}_{timestamp}.log"

    if log_name in DOCKER_CONTAINERS:
        content = await _read_docker_logs(DOCKER_CONTAINERS[log_name], lines=50000)
        return StreamingResponse(
            iter([content]),
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    file_path = _resolve_file_path(log_name)
    if not file_path or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Log file not found: {log_name}")

    def _file_iterator() -> Iterator[str]:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            yield from f

    return StreamingResponse(
        _file_iterator(),
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
