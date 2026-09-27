"""Admin Dashboard Router.

Web-based metrics dashboard for Gmail Inbox Bot email processing.
Protected with the same cookie-based auth as the log viewer.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import httpx2
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

JEV_CATEGORIES = (
    "personal",
    "finanzas",
    "compras",
    "newsletters",
    "notificaciones",
    "automatico",
    "spam",
    "otros",
)
_JEV_SELECT = (
    "mailbox,category,jev_category,jev_confidence,jev_error,model,sender,subject,created_at"
)
_CONFIDENCE_BUCKETS = (
    ("<0.5", 0.0, 0.5),
    ("0.5-0.7", 0.5, 0.7),
    ("0.7-0.9", 0.7, 0.9),
    (">=0.9", 0.9, 1.01),
)
_MAX_MISMATCHES = 30


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
    *,
    select: str = "mailbox,category,created_at",
    extra_params: dict[str, str] | None = None,
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
        "select": select,
        "order": "created_at.asc",
        **(extra_params or {}),
    }
    if date_from:
        params["created_at"] = f"gte.{date_from}"
    if date_to:
        end_exclusive = (date.fromisoformat(date_to) + timedelta(days=1)).isoformat()
        key_name = "created_at" if "created_at" not in params else "and"
        if key_name == "and":
            params.pop("created_at")
            params["and"] = f"(created_at.gte.{date_from},created_at.lt.{end_exclusive})"
        else:
            params["created_at"] = f"lt.{end_exclusive}"
    if mailbox:
        params["mailbox"] = f"eq.{mailbox}"

    all_rows: list[dict] = []
    offset = 0

    async with httpx2.AsyncClient(timeout=15) as client:
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


def _pct(part: int, whole: int) -> float:
    """Porcentaje redondeado a un decimal; 0.0 si el denominador es cero."""
    return round(100.0 * part / whole, 1) if whole else 0.0


def _decided_by_jev(row: dict) -> bool:
    """True si la clasificación de esa fila la decidió Jev, no el LLM."""
    return str(row.get("model") or "").startswith("jev")


def _aggregate_jev(rows: list[dict]) -> dict:
    """Compara la categoría del LLM (``category``) con la de Jev (``jev_category``).

    Solo entran las filas en las que **clasificó el LLM** y Jev opinó al lado. Desde que Jev
    es el clasificador principal (2026-09-27), en sus filas ``category == jev_category`` por
    construcción: contarlas daría un 100 % de coincidencia falso y vaciaría la lista de
    discrepancias. Se cuentan aparte en ``decided_by_jev`` para que se vea que la muestra de
    comparación ya no crece.

    Las filas con ``jev_error`` cuentan como errores y no entran en la coincidencia.
    """
    decided_by_jev = sum(1 for r in rows if _decided_by_jev(r))
    comparable = [r for r in rows if not _decided_by_jev(r)]
    compared = [r for r in comparable if r.get("jev_category") and not r.get("jev_error")]
    errors = sum(1 for r in comparable if r.get("jev_error"))

    agreed = sum(1 for r in compared if r.get("category") == r.get("jev_category"))

    by_confidence = []
    for label, low, high in _CONFIDENCE_BUCKETS:
        bucket = [
            r
            for r in compared
            if r.get("jev_confidence") is not None and low <= float(r["jev_confidence"]) < high
        ]
        bucket_agreed = sum(1 for r in bucket if r.get("category") == r.get("jev_category"))
        by_confidence.append(
            {"label": label, "n": len(bucket), "agreement_pct": _pct(bucket_agreed, len(bucket))}
        )

    confusion = {llm: dict.fromkeys(JEV_CATEGORIES, 0) for llm in JEV_CATEGORIES}
    for r in compared:
        llm, jev = r.get("category"), r.get("jev_category")
        if llm in confusion and jev in confusion[llm]:
            confusion[llm][jev] += 1

    mismatches = sorted(
        (r for r in compared if r.get("category") != r.get("jev_category")),
        key=lambda r: r.get("created_at", ""),
        reverse=True,
    )[:_MAX_MISMATCHES]
    mismatches = [
        {
            "created_at": r.get("created_at", ""),
            "mailbox": r.get("mailbox", ""),
            "sender": r.get("sender", ""),
            "subject": r.get("subject", ""),
            "category": r.get("category", ""),
            "jev_category": r.get("jev_category", ""),
            "jev_confidence": r.get("jev_confidence"),
        }
        for r in mismatches
    ]

    return {
        "total": len(compared),
        "agreement_pct": _pct(agreed, len(compared)),
        "errors": errors,
        "decided_by_jev": decided_by_jev,
        "by_confidence": by_confidence,
        "confusion": confusion,
        "categories": list(JEV_CATEGORIES),
        "mismatches": mismatches,
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/dashboard", response_class=HTMLResponse, response_model=None)
async def dashboard_page(request: Request) -> HTMLResponse | RedirectResponse:
    """Serve the dashboard HTML page."""
    if not _is_authenticated(request):
        return RedirectResponse(url="/admin/logs", status_code=302)

    return templates.TemplateResponse(request, "admin_dashboard.html")


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


@router.get("/api/jev_shadow")
async def api_jev_shadow(
    request: Request,
    date_from: str | None = Query(None, description="Start date (YYYY-MM-DD)"),
    date_to: str | None = Query(None, description="End date (YYYY-MM-DD)"),
    mailbox: str | None = Query(None, description="Filter by mailbox name"),
) -> dict:
    """Comparativa Jev (sombra) vs clasificador LLM."""
    if not _is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        rows = await _fetch_metrics(
            date_from,
            date_to,
            mailbox,
            select=_JEV_SELECT,
            extra_params={"or": "(jev_category.not.is.null,jev_error.not.is.null)"},
        )
    except Exception as exc:
        logger.error("Error fetching Jev shadow metrics from Supabase: %s", exc)
        raise HTTPException(status_code=502, detail="Error fetching metrics") from exc

    result = _aggregate_jev(rows)
    result["filters"] = {"date_from": date_from, "date_to": date_to, "mailbox": mailbox}
    return result
