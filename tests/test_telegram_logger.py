"""Tests for telegram_logger.py — logging handler."""

import logging
from unittest.mock import patch

from gmail_inbox_bot.telegram_logger import TelegramHandler, setup_telegram_logging


class TestTelegramHandler:
    def _make_record(self, name="gmail_inbox_bot.bot", level=logging.ERROR, msg="boom"):
        logger = logging.getLogger(name)
        record = logger.makeRecord(name, level, "bot.py", 42, msg, (), None, func="process")
        return record

    @patch("gmail_inbox_bot.telegram_logger.enviar_mensaje_telegram")
    def test_emits_error(self, mock_send):
        handler = TelegramHandler(chat_id="123")
        record = self._make_record()
        handler.emit(record)
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "boom" in msg
        assert "\U0001f6a8" in msg

    @patch("gmail_inbox_bot.telegram_logger.enviar_mensaje_telegram")
    def test_emits_critical_with_different_emoji(self, mock_send):
        handler = TelegramHandler(chat_id="123")
        record = self._make_record(level=logging.CRITICAL)
        handler.emit(record)
        msg = mock_send.call_args[0][0]
        assert "\U0001f4a5" in msg

    @patch("gmail_inbox_bot.telegram_logger.enviar_mensaje_telegram")
    def test_skips_warning(self, mock_send):
        handler = TelegramHandler(chat_id="123")
        record = self._make_record(level=logging.WARNING)
        handler.emit(record)
        mock_send.assert_not_called()

    @patch("gmail_inbox_bot.telegram_logger.enviar_mensaje_telegram")
    def test_skips_telegram_logger(self, mock_send):
        handler = TelegramHandler(chat_id="123")
        record = self._make_record(name="gmail_inbox_bot.telegram")
        handler.emit(record)
        mock_send.assert_not_called()

    @patch("gmail_inbox_bot.telegram_logger.enviar_mensaje_telegram")
    def test_includes_exception_info(self, mock_send):
        handler = TelegramHandler(chat_id="123")
        try:
            raise ValueError("test error")
        except ValueError:
            logger = logging.getLogger("gmail_inbox_bot.bot")
            record = logger.makeRecord(
                "gmail_inbox_bot.bot",
                logging.ERROR,
                "bot.py",
                1,
                "failed",
                (),
                None,
                func="run",
            )
            import sys

            record.exc_info = sys.exc_info()
        handler.emit(record)
        msg = mock_send.call_args[0][0]
        assert "ValueError" in msg
        assert "test error" in msg

    @patch(
        "gmail_inbox_bot.telegram_logger.enviar_mensaje_telegram",
        side_effect=Exception("kaboom"),
    )
    def test_emit_never_raises(self, mock_send):
        handler = TelegramHandler(chat_id="123")
        record = self._make_record()
        handler.emit(record)  # should not raise

    @patch("gmail_inbox_bot.telegram_logger.enviar_mensaje_telegram")
    def test_passes_chat_id(self, mock_send):
        handler = TelegramHandler(chat_id="456")
        handler.emit(self._make_record())
        assert mock_send.call_args[0][1] == "456"


class TestSetupTelegramLogging:
    def test_attaches_handler(self):
        setup_telegram_logging(chat_id="789")
        target_loggers = [
            "gmail_inbox_bot.bot",
            "gmail_inbox_bot.actions",
            "gmail_inbox_bot.gmail_client",
            "gmail_inbox_bot.classifier",
        ]
        for name in target_loggers:
            logger = logging.getLogger(name)
            telegram_handlers = [h for h in logger.handlers if isinstance(h, TelegramHandler)]
            assert len(telegram_handlers) >= 1, f"No TelegramHandler on {name}"

        # Cleanup
        for name in target_loggers:
            logger = logging.getLogger(name)
            logger.handlers = [h for h in logger.handlers if not isinstance(h, TelegramHandler)]
