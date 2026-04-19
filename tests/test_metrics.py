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
