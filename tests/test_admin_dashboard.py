"""Tests for admin_dashboard aggregations (sin red)."""

from gmail_inbox_bot.admin_dashboard import JEV_CATEGORIES, _aggregate_jev


def _row(category, jev_category, confidence, *, error=None, created="2026-09-21T10:00:00"):
    return {
        "category": category,
        "jev_category": jev_category,
        "jev_confidence": confidence,
        "jev_error": error,
        "sender": "a@b.c",
        "subject": "Asunto",
        "created_at": created,
        "mailbox": "jesus82c",
    }


class TestAggregateJev:
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
        ]
        result = _aggregate_jev(rows)
        assert result["total"] == 1
        assert result["errors"] == 1
        assert result["agreement_pct"] == 100.0

    def test_unknown_category_is_ignored_in_confusion_but_counted(self):
        rows = [_row("pre_filter:x", "spam", 0.9)]
        result = _aggregate_jev(rows)
        assert result["total"] == 1
        assert "pre_filter:x" not in result["confusion"]
