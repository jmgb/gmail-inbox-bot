"""Tests for admin_dashboard aggregations (sin red)."""

from datetime import datetime

import httpx2
import pytest
from fastapi.testclient import TestClient

from gmail_inbox_bot import admin_dashboard
from gmail_inbox_bot.admin_dashboard import JEV_CATEGORIES, _aggregate_jev
from gmail_inbox_bot.admin_logs import SESSION_COOKIE, _make_session_cookie
from gmail_inbox_bot.app import app


def _row(
    category,
    jev_category,
    confidence,
    *,
    error=None,
    created="2026-09-21T10:00:00",
    model="openai/gpt-oss-120b",
):
    return {
        "category": category,
        "jev_category": jev_category,
        "jev_confidence": confidence,
        "jev_error": error,
        "model": model,
        "sender": "a@b.c",
        "subject": "Asunto",
        "created_at": created,
        "mailbox": "jesus82c",
    }


class TestAggregateJev:
    def test_excluye_las_filas_que_decidio_jev(self):
        """Desde el corte a Jev, category == jev_category por construcción.

        Contarlas daría un 100 % de coincidencia falso y vaciaría la lista de
        discrepancias justo cuando ya no hay con qué comparar. Solo cuentan las filas en
        las que clasificó el LLM y Jev opinó al lado.
        """
        rows = [
            _row("spam", "spam", 0.95, model="jev-1.13.0"),
            _row("newsletters", "newsletters", 0.99, model="jev-1.13.0"),
            _row("spam", "newsletters", 0.6),
        ]

        result = _aggregate_jev(rows)

        assert result["total"] == 1
        assert result["agreement_pct"] == 0.0
        assert len(result["mismatches"]) == 1
        assert result["decided_by_jev"] == 2

    def test_empty_rows(self):
        result = _aggregate_jev([])
        assert result["total"] == 0
        assert result["agreement_pct"] == 0.0
        assert result["errors"] == 0
        assert result["mismatches"] == []
        assert [bucket["label"] for bucket in result["by_confidence"]] == [
            "<0.5",
            "0.5-0.7",
            "0.7-0.9",
            ">=0.9",
        ]

    def test_agreement_and_confusion(self):
        rows = [
            _row("spam", "spam", 0.95),
            _row("spam", "newsletters", 0.6),
            _row("otros", "otros", 0.3),
            _row("finanzas", "finanzas", 0.8),
        ]
        result = _aggregate_jev(rows)
        assert result["total"] == 4
        assert result["agreement_pct"] == 75.0
        assert result["confusion"]["spam"]["spam"] == 1
        assert result["confusion"]["spam"]["newsletters"] == 1
        assert result["confusion"]["otros"]["otros"] == 1
        assert set(result["confusion"]) == set(JEV_CATEGORIES)

    def test_confidence_buckets(self):
        rows = [
            _row("spam", "spam", 0.95),
            _row("spam", "spam", 0.92),
            _row("spam", "otros", 0.75),
            _row("otros", "otros", 0.55),
            _row("otros", "spam", 0.1),
        ]
        buckets = {b["label"]: b for b in _aggregate_jev(rows)["by_confidence"]}
        assert buckets[">=0.9"] == {"label": ">=0.9", "n": 2, "agreement_pct": 100.0}
        assert buckets["0.7-0.9"] == {"label": "0.7-0.9", "n": 1, "agreement_pct": 0.0}
        assert buckets["0.5-0.7"] == {"label": "0.5-0.7", "n": 1, "agreement_pct": 100.0}
        assert buckets["<0.5"] == {"label": "<0.5", "n": 1, "agreement_pct": 0.0}

    def test_confidence_bucket_boundaries(self):
        rows = [
            _row("spam", "spam", 0.5),
            _row("spam", "spam", 0.7),
            _row("spam", "spam", 0.9),
            _row("spam", "spam", 1.0),
            _row("spam", "spam", None),
        ]
        result = _aggregate_jev(rows)
        buckets = {b["label"]: b["n"] for b in result["by_confidence"]}
        assert buckets == {"<0.5": 0, "0.5-0.7": 1, "0.7-0.9": 1, ">=0.9": 2}
        assert result["total"] == 5
        assert sum(buckets.values()) == result["total"] - 1

    def test_mismatches_newest_first_and_capped(self):
        rows = [
            _row("spam", "otros", 0.5, created=f"2026-09-{day:02d}T10:00:00")
            for day in range(1, 31)
        ] + [_row("spam", "otros", 0.5, created="2026-10-05T10:00:00")]
        result = _aggregate_jev(rows)
        assert len(result["mismatches"]) == 30
        assert result["mismatches"][0]["created_at"] == "2026-10-05T10:00:00"
        assert result["mismatches"][0]["category"] == "spam"
        assert result["mismatches"][0]["jev_category"] == "otros"

    def test_errors_counted_and_excluded_from_agreement(self):
        rows = [
            _row("spam", "spam", 0.9),
            _row("spam", None, None, error="TypeSafeAPITimeoutError: timeout"),
            _row("spam", "otros", 0.6, error="TypeSafeAPITimeoutError: retry timeout"),
        ]
        result = _aggregate_jev(rows)
        assert result["total"] == 1
        assert result["errors"] == 2
        assert result["agreement_pct"] == 100.0
        assert result["confusion"]["spam"]["otros"] == 0
        assert result["mismatches"] == []
        assert sum(bucket["n"] for bucket in result["by_confidence"]) == 1

    def test_unknown_category_is_ignored_in_confusion_but_counted(self):
        rows = [_row("pre_filter:x", "spam", 0.9)]
        result = _aggregate_jev(rows)
        assert result["total"] == 1
        assert "pre_filter:x" not in result["confusion"]


@pytest.fixture
def metrics_api(monkeypatch):
    rows = []
    requests = []

    def serve(request):
        requests.append(request)
        params = request.url.params
        selected = rows
        if "or" in params:
            fields = [term.split(".")[0] for term in params["or"].strip("()").split(",")]
            selected = [
                row for row in selected if any(row.get(field) is not None for field in fields)
            ]
        if "mailbox" in params:
            selected = [
                row for row in selected if row["mailbox"] == params["mailbox"].removeprefix("eq.")
            ]
        bounds = []
        if "created_at" in params:
            bounds.append(params["created_at"])
        if "and" in params:
            bounds.extend(
                term.removeprefix("created_at.") for term in params["and"].strip("()").split(",")
            )
        for bound in bounds:
            operator, value = bound.split(".", 1)
            threshold = datetime.fromisoformat(value)
            compare = {
                "gte": lambda stamp: stamp >= threshold,
                "lte": lambda stamp: stamp <= threshold,
                "lt": lambda stamp: stamp < threshold,
            }[operator]
            selected = [
                row for row in selected if compare(datetime.fromisoformat(row["created_at"]))
            ]
        selected = sorted(selected, key=lambda row: row["created_at"])
        offset, limit = int(params["offset"]), int(params["limit"])
        fields = params["select"].split(",")
        return httpx2.Response(
            200,
            json=[
                {field: row.get(field) for field in fields}
                for row in selected[offset : offset + limit]
            ],
        )

    original_client = httpx2.AsyncClient
    transport = httpx2.MockTransport(serve)
    monkeypatch.setattr(
        admin_dashboard.httpx2,
        "AsyncClient",
        lambda **kwargs: original_client(transport=transport, **kwargs),
    )
    monkeypatch.setenv("SUPABASE_URL", "https://metrics.example.test")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "test-key")
    monkeypatch.setenv("LOGS_VIEWER_PASSWORD", "secret")
    monkeypatch.setenv("DISABLE_BOT", "1")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, _make_session_cookie("secret"))
        yield client, rows, requests


def test_jev_endpoint_includes_entire_end_date_and_filters_errors(metrics_api):
    client, rows, _requests = metrics_api
    rows.extend(
        [
            _row("spam", "spam", 0.9),
            _row("spam", "otros", 0.6, created="2026-09-21T23:59:59.500000"),
            _row("spam", None, None, error="inside", created="2026-09-21T23:59:59.999999"),
            _row("spam", None, None, error="before", created="2026-09-20T23:59:59"),
            _row("spam", None, None, error="after", created="2026-09-22T00:00:00"),
            {**_row("spam", None, None, error="other mailbox"), "mailbox": "other"},
        ]
    )

    response = client.get(
        "/admin/api/jev_shadow",
        params={"date_from": "2026-09-21", "date_to": "2026-09-21", "mailbox": "jesus82c"},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 2
    assert result["errors"] == 1
    assert result["agreement_pct"] == 50.0
    assert result["mismatches"][0]["created_at"] == "2026-09-21T23:59:59.500000"


@pytest.mark.parametrize(
    ("params", "expected_total", "expected_errors"),
    [
        ({"date_from": "2026-09-21"}, 2, 1),
        ({"date_to": "2026-09-21"}, 1, 2),
    ],
)
def test_jev_endpoint_one_sided_dates(metrics_api, params, expected_total, expected_errors):
    client, rows, _requests = metrics_api
    rows.extend(
        [
            _row("spam", None, None, error="before", created="2026-09-20T23:59:59"),
            _row("spam", "spam", 0.9, created="2026-09-21T23:59:59.999999"),
            _row("spam", None, None, error="inside", created="2026-09-21T23:59:59.999999"),
            _row("spam", "spam", 0.9, created="2026-09-22T00:00:00"),
        ]
    )

    response = client.get("/admin/api/jev_shadow", params=params)

    assert response.status_code == 200
    assert response.json()["total"] == expected_total
    assert response.json()["errors"] == expected_errors


def test_jev_endpoint_paginates_comparisons_and_errors(metrics_api):
    client, rows, requests = metrics_api
    rows.extend(_row("spam", "spam", 0.95) for _ in range(1001))
    rows.append(_row("spam", None, None, error="last page", created="2026-09-21T11:00:00"))
    rows.append(_row("spam", None, None, created="2026-09-21T12:00:00"))

    response = client.get("/admin/api/jev_shadow")

    assert response.status_code == 200
    result = response.json()
    assert result["total"] == 1001
    assert result["errors"] == 1
    assert result["confusion"]["spam"]["spam"] == 1001
    assert result["by_confidence"][-1]["n"] == 1001
    assert all(int(request.url.params["limit"]) <= 1000 for request in requests)


def test_jev_endpoint_rejects_invalid_session_before_fetching(metrics_api):
    client, _rows, requests = metrics_api
    client.cookies.set(SESSION_COOKIE, "invalid")

    response = client.get("/admin/api/jev_shadow")

    assert response.status_code == 401
    assert requests == []
