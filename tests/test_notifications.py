"""Tests for notifications.py — important email alerts."""

from unittest.mock import patch

from gmail_inbox_bot.notifications import NOTIFY_CATEGORIES, notify_important_email


class TestNotifyImportantEmail:
    @patch("gmail_inbox_bot.notifications.enviar_mensaje_telegram")
    def test_personal_notification(self, mock_send):
        notify_important_email(
            mailbox="test@gmail.com",
            categoria="personal",
            sender="Juan <juan@example.com>",
            subject="Pregunta importante",
            razon="Email directo de persona real",
        )
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "personal" in msg
        assert "Juan" in msg
        assert "Pregunta importante" in msg
        assert "\U0001f4e9" in msg

    @patch("gmail_inbox_bot.notifications.enviar_mensaje_telegram")
    def test_finanzas_notification(self, mock_send):
        notify_important_email(
            mailbox="test@gmail.com",
            categoria="finanzas",
            sender="banco@bbva.es",
            subject="Movimiento en cuenta",
        )
        msg = mock_send.call_args[0][0]
        assert "finanzas" in msg
        assert "\U0001f4b0" in msg

    @patch("gmail_inbox_bot.notifications.enviar_mensaje_telegram")
    def test_without_razon(self, mock_send):
        notify_important_email(
            mailbox="test@gmail.com",
            categoria="personal",
            sender="x@y.com",
            subject="Hi",
        )
        msg = mock_send.call_args[0][0]
        assert "Razón" not in msg


class TestNotifyCategories:
    def test_contains_personal_and_finanzas(self):
        assert "personal" in NOTIFY_CATEGORIES
        assert "finanzas" in NOTIFY_CATEGORIES

    def test_does_not_contain_spam(self):
        assert "spam" not in NOTIFY_CATEGORIES
