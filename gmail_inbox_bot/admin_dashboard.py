"""Admin Dashboard Router.

Web-based metrics dashboard for Gmail Inbox Bot email processing.
Protected with the same cookie-based auth as the log viewer.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .admin_logs import SESSION_COOKIE, _get_password, _validate_session_cookie
from .logger import get_logger
from .metrics import SUPABASE_TABLE

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-dashboard"])

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_PAGE_SIZE = 1000


def _is_authenticated(request: Request) -> bool:
    """Check if the request has a valid session cookie."""
    password = _get_password()
    if not password:
        return False
    cookie = request.cookies.get(SESSION_COOKIE, "")
    return bool(cookie and _validate_session_cookie(cookie, password))


async def _fetch_metrics(
    date_from: str | None,
    date_to: str | None,
    mailbox: str | None,
) -> list[dict]:
    """Fetch all matching rows from Supabase, paginating as needed."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SECRET_KEY", "")

    if not url or not key:
        logger.warning("SUPABASE_URL or SUPABASE_SECRET_KEY not set — returning empty metrics")
        return []

    endpoint = f"{url}/rest/v1/{SUPABASE_TABLE}"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "count=exact",
    }

    params: dict[str, str] = {
        "select": "mailbox,category,created_at",
        "order": "created_at.asc",
    }
    if date_from:
        params["created_at"] = f"gte.{date_from}"
    if date_to:
        key_name = "created_at" if "created_at" not in params else "and"
        if key_name == "and":
            params.pop("created_at")
            params["and"] = f"(created_at.gte.{date_from},created_at.lte.{date_to}T23:59:59)"
        else:
            params["created_at"] = f"lte.{date_to}T23:59:59"
    if mailbox:
        params["mailbox"] = f"eq.{mailbox}"

    all_rows: list[dict] = []
    offset = 0

    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            page_params = {**params, "offset": str(offset), "limit": str(_PAGE_SIZE)}
            resp = await client.get(endpoint, headers=headers, params=page_params)
            resp.raise_for_status()
            rows = resp.json()
            all_rows.extend(rows)
            if len(rows) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

    return all_rows


def _aggregate(rows: list[dict]) -> dict:
    """Compute aggregations from raw rows."""
    total = len(rows)

    mailbox_counter: Counter[str] = Counter()
    category_counter: Counter[tuple[str, str]] = Counter()
    date_counter: Counter[str] = Counter()

    for row in rows:
        mb = row.get("mailbox", "unknown")
        cat = row.get("category", "unknown")
        created = row.get("created_at", "")

        mailbox_counter[mb] += 1
        category_counter[(mb, cat)] += 1

        date_str = created[:10] if len(created) >= 10 else created
        if date_str:
            date_counter[date_str] += 1

    by_mailbox = [{"mailbox": mb, "count": c} for mb, c in mailbox_counter.most_common()]

    by_category = [
        {"mailbox": mb, "category": cat, "count": c}
        for (mb, cat), c in category_counter.most_common()
    ]

    by_date = [{"date": d, "count": c} for d, c in sorted(date_counter.items())]

    return {
        "total": total,
        "by_mailbox": by_mailbox,
        "by_category": by_category,
        "by_date": by_date,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_class=HTMLResponse, response_model=None)
async def dashboard_page(request: Request) -> HTMLResponse | RedirectResponse:
    """Serve the dashboard HTML page."""
    if not _is_authenticated(request):
        return RedirectResponse(url="/admin/logs", status_code=302)

    return templates.TemplateResponse("admin_dashboard.html", {"request": request})


@router.get("/api/metrics")
async def api_metrics(
    request: Request,
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    mailbox: str | None = Query(None, description="Filter by mailbox name"),
) -> dict:
    """JSON API endpoint returning aggregated dashboard metrics."""
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        rows = await _fetch_metrics(date_from, date_to, mailbox)
    except Exception as exc:
        logger.error("Error fetching metrics from Supabase: %s", exc)
        raise HTTPException(status_code=502, detail="Error fetching metrics") from exc

    result = _aggregate(rows)
    result["filters"] = {
        "date_from": date_from,
        "date_to": date_to,
        "mailbox": mailbox,
    }
    return result
