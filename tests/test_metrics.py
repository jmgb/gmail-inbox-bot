"""Tests for metrics payload persistence."""

from unittest.mock import patch

from gmail_inbox_bot.metrics import record_email


class TestRecordEmail:
    @patch("gmail_inbox_bot.metrics._supabase_upsert")
    def test_cost_fields_are_included_in_payload(self, mock_upsert):
        record_email(
            mailbox="test",
            category="otros",
            action="reply",
            msg_id="msg-1",
            model="openai/gpt-oss-120b",
            input_tokens=1200,
            output_tokens=300,
            total_tokens=1500,
            input_cost_usd=0.00018,
            output_cost_usd=0.00018,
            total_cost_usd=0.00036,
            llm_provider="Groq",
        )

        payload = mock_upsert.call_args.args[0]
        assert payload["input_tokens"] == 1200
        assert payload["output_tokens"] == 300
        assert payload["total_tokens"] == 1500
        assert payload["input_cost_usd"] == 0.00018
        assert payload["output_cost_usd"] == 0.00018
        assert payload["total_cost_usd"] == 0.00036
        assert payload["llm_provider"] == "Groq"

    @patch("gmail_inbox_bot.metrics._supabase_upsert")
    def test_jev_fields_are_included_in_payload(self, mock_upsert):
        record_email(
            mailbox="test",
            category="spam",
            msg_id="msg-2",
            jev_category="spam",
            jev_confidence=0.93,
            jev_probabilities={"spam": 0.93, "otros": 0.07},
            jev_latency_ms=901,
            jev_model="jev-1.13.0",
        )

        payload = mock_upsert.call_args.args[0]
        assert payload["jev_category"] == "spam"
        assert payload["jev_confidence"] == 0.93
        assert payload["jev_probabilities"] == {"spam": 0.93, "otros": 0.07}
        assert payload["jev_latency_ms"] == 901
        assert payload["jev_model"] == "jev-1.13.0"
        assert "jev_error" not in payload

    @patch("gmail_inbox_bot.metrics._supabase_upsert")
    def test_jev_error_is_included_and_other_jev_fields_omitted(self, mock_upsert):
        record_email(
            mailbox="test",
            category="spam",
            msg_id="msg-3",
            jev_error="TypeSafeAPITimeoutError: timeout",
            jev_latency_ms=8000,
        )

        payload = mock_upsert.call_args.args[0]
        assert payload["jev_error"] == "TypeSafeAPITimeoutError: timeout"
        assert payload["jev_latency_ms"] == 8000
        assert "jev_category" not in payload
        assert "jev_confidence" not in payload

    @patch("gmail_inbox_bot.metrics._supabase_upsert")
    def test_jev_fields_absent_when_not_provided(self, mock_upsert):
        record_email(mailbox="test", category="otros", msg_id="msg-4")

        payload = mock_upsert.call_args.args[0]
        assert not any(key.startswith("jev_") for key in payload)
