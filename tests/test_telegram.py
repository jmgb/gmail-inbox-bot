"""Tests for telegram.py — transport layer."""

from unittest.mock import MagicMock, patch

import httpx2

from gmail_inbox_bot.telegram import (
    _retry_delay,
    _send_chunk,
    _split_message,
    enviar_mensaje_telegram,
    escapar_caracteres,
)


class TestEscaparCaracteres:
    def test_escapes_ampersand(self):
        assert "&amp;" in escapar_caracteres("A & B")

    def test_escapes_angle_brackets(self):
        assert "&lt;script&gt;" in escapar_caracteres("<script>")

    def test_preserves_bold(self):
        result = escapar_caracteres("<b>bold</b>")
        assert "<b>bold</b>" in result

    def test_preserves_italic(self):
        result = escapar_caracteres("<i>italic</i>")
        assert "<i>italic</i>" in result

    def test_preserves_pre(self):
        result = escapar_caracteres("<pre>code</pre>")
        assert "<pre>code</pre>" in result

    def test_preserves_link(self):
        result = escapar_caracteres('<a href="https://x.com">link</a>')
        assert 'href="https://x.com"' in result
        assert "</a>" in result


class TestSplitMessage:
    def test_short_message_single_chunk(self):
        assert _split_message("hello") == ["hello"]

    def test_long_message_splits(self):
        msg = "x" * 7500
        parts = _split_message(msg)
        assert len(parts) == 3
        assert "<b>…</b>" in parts[0]
        assert "<b>…</b>" not in parts[-1]


class TestRetryDelay:
    def test_exponential_backoff(self):
        assert _retry_delay(1) == 2
        assert _retry_delay(2) == 4
        assert _retry_delay(3) == 8

    def test_capped_at_max(self):
        assert _retry_delay(10) == 30

    def test_429_retry_after(self):
        resp = MagicMock()
        resp.status_code = 429
        resp.json.return_value = {"parameters": {"retry_after": 5}}
        assert _retry_delay(1, resp) == 5


class TestSendChunk:
    @patch("gmail_inbox_bot.telegram.httpx2.post")
    def test_success(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        ok, err = _send_chunk(url="http://x", payload={}, referencia="test", max_attempts=1)
        assert ok is True
        assert err == ""

    @patch("gmail_inbox_bot.telegram.httpx2.post")
    def test_non_retryable_error(self, mock_post):
        resp = MagicMock(status_code=400, text="bad request")
        resp.json.return_value = {"description": "bad request"}
        mock_post.return_value = resp
        ok, err = _send_chunk(url="http://x", payload={}, referencia="test", max_attempts=2)
        assert ok is False
        mock_post.assert_called_once()

    @patch("gmail_inbox_bot.telegram.time.sleep")
    @patch("gmail_inbox_bot.telegram.httpx2.post")
    def test_retryable_error_retries(self, mock_post, mock_sleep):
        fail = MagicMock(status_code=500, text="error")
        fail.json.return_value = {"description": "error"}
        success = MagicMock(status_code=200)
        mock_post.side_effect = [fail, success]
        ok, _ = _send_chunk(url="http://x", payload={}, referencia="test", max_attempts=2)
        assert ok is True
        assert mock_post.call_count == 2

    @patch("gmail_inbox_bot.telegram.httpx2.post")
    def test_network_error(self, mock_post):
        mock_post.side_effect = httpx2.ConnectError("timeout")
        ok, err = _send_chunk(url="http://x", payload={}, referencia="test", max_attempts=1)
        assert ok is False
        assert "timeout" in err


class TestEnviarMensajeTelegram:
    @patch("gmail_inbox_bot.telegram._send_chunk", return_value=(True, ""))
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_sends_message(self, mock_send):
        enviar_mensaje_telegram("hello", referencia="test")
        mock_send.assert_called_once()
        payload = mock_send.call_args[1]["payload"]
        assert payload["chat_id"] == "123"
        assert "[Gmail Bot]" in payload["text"]

    @patch("gmail_inbox_bot.telegram._send_chunk")
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "", "TELEGRAM_CHAT_ID": "123"})
    def test_no_token_skips(self, mock_send):
        enviar_mensaje_telegram("hello")
        mock_send.assert_not_called()

    @patch("gmail_inbox_bot.telegram._send_chunk")
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": ""})
    def test_no_chat_id_skips(self, mock_send):
        enviar_mensaje_telegram("hello")
        mock_send.assert_not_called()

    @patch("gmail_inbox_bot.telegram._send_chunk", return_value=(True, ""))
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_explicit_chat_id(self, mock_send):
        enviar_mensaje_telegram("hello", "999")
        payload = mock_send.call_args[1]["payload"]
        assert payload["chat_id"] == "999"


class TestTelegramActivity:
    """La captura para el triage registra una línea por mensaje lógico."""

    @patch("gmail_inbox_bot.telegram.telegram_activity")
    @patch("gmail_inbox_bot.telegram._send_chunk", return_value=(True, ""))
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_registers_once_per_message(self, mock_send, mock_activity):
        enviar_mensaje_telegram("⚠️ hola", referencia="reminder_failure")
        mock_activity.registrar.assert_called_once_with(
            "⚠️ hola", referencia="reminder_failure", nivel=None
        )

    @patch("gmail_inbox_bot.telegram.telegram_activity")
    @patch("gmail_inbox_bot.telegram.time.sleep")
    @patch("gmail_inbox_bot.telegram._send_chunk", return_value=(True, ""))
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_registers_once_even_when_chunked(self, mock_send, mock_sleep, mock_activity):
        enviar_mensaje_telegram("x" * 7500, referencia="test")
        assert mock_send.call_count == 3
        mock_activity.registrar.assert_called_once()

    @patch("gmail_inbox_bot.telegram.telegram_activity")
    @patch("gmail_inbox_bot.telegram.time.sleep")
    @patch("gmail_inbox_bot.telegram.httpx2.post")
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_registers_once_despite_retries(self, mock_post, mock_sleep, mock_activity):
        fail = MagicMock(status_code=500, text="error")
        fail.json.return_value = {"description": "error"}
        mock_post.side_effect = [fail, MagicMock(status_code=200)]
        enviar_mensaje_telegram("hola", referencia="test")
        assert mock_post.call_count == 2
        mock_activity.registrar.assert_called_once()

    @patch("gmail_inbox_bot.telegram.telegram_activity")
    @patch("gmail_inbox_bot.telegram._send_chunk")
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "", "TELEGRAM_CHAT_ID": "123"})
    def test_not_registered_when_not_configured(self, mock_send, mock_activity):
        enviar_mensaje_telegram("hola")
        mock_activity.registrar.assert_not_called()

    @patch("gmail_inbox_bot.telegram.telegram_activity")
    @patch("gmail_inbox_bot.telegram._send_chunk", return_value=(True, ""))
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_activity_failure_does_not_block_send(self, mock_send, mock_activity):
        mock_activity.registrar.side_effect = RuntimeError("disco lleno")
        enviar_mensaje_telegram("hola", referencia="test")
        mock_send.assert_called_once()

    @patch("gmail_inbox_bot.telegram.telegram_activity", None)
    @patch("gmail_inbox_bot.telegram._send_chunk", return_value=(True, ""))
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_missing_helper_still_sends(self, mock_send):
        enviar_mensaje_telegram("hola", referencia="test")
        mock_send.assert_called_once()

    @patch("gmail_inbox_bot.telegram.telegram_activity")
    @patch("gmail_inbox_bot.telegram._send_chunk", return_value=(True, ""))
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_explicit_level_is_forwarded(self, mock_send, mock_activity):
        enviar_mensaje_telegram("\U0001f534 SOLD AIXI", referencia="ib_trade", nivel="ok")
        assert mock_activity.registrar.call_args.kwargs["nivel"] == "ok"

    @patch("gmail_inbox_bot.telegram.telegram_activity")
    @patch("gmail_inbox_bot.telegram._send_chunk", return_value=(True, ""))
    @patch.dict("os.environ", {"TELEGRAM_TOKEN": "tok", "TELEGRAM_CHAT_ID": "123"})
    def test_level_defaults_to_inferred(self, mock_send, mock_activity):
        enviar_mensaje_telegram("hola", referencia="test")
        assert mock_activity.registrar.call_args.kwargs["nivel"] is None
