"""TelegramHandler — logging handler that sends ERROR+ records to Telegram."""

from __future__ import annotations

import logging
import traceback

from .telegram import enviar_mensaje_telegram, escapar_caracteres


class TelegramHandler(logging.Handler):
    """Sends ERROR and CRITICAL log records to Telegram automatically."""

    def __init__(self, chat_id: str | None = None, level: int = logging.ERROR):
        super().__init__(level)
        self.chat_id = chat_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.levelno < logging.ERROR:
                return
            # Prevent infinite loop: don't send telegram-related errors to Telegram
            if "telegram" in record.name:
                return

            module = record.name if record.name != "__main__" else "gmail_inbox_bot"
            func = record.funcName if record.funcName != "<module>" else ""
            text = escapar_caracteres(record.getMessage())
            emoji = "\U0001f6a8" if record.levelno == logging.ERROR else "\U0001f4a5"

            if func:
                msg = f"{emoji} <b>[{module}:{func}]</b> {text}"
            else:
                msg = f"{emoji} <b>[{module}]</b> {text}"

            if record.exc_info and record.exc_info[1]:
                exc_type = type(record.exc_info[1]).__name__
                exc_msg = escapar_caracteres(str(record.exc_info[1]))
                tb_lines = traceback.format_tb(record.exc_info[2])
                tb_short = escapar_caracteres("".join(tb_lines[-3:]).strip())
                msg += f"\n\n<b>Exception:</b> {exc_type}: {exc_msg}"
                if tb_short:
                    msg += f"\n<pre>{tb_short}</pre>"

            enviar_mensaje_telegram(msg, self.chat_id, referencia="telegram_logger")
        except Exception:
            pass  # never break the app because of notifications


def setup_telegram_logging(chat_id: str | None = None) -> None:
    """Attach TelegramHandler to gmail_inbox_bot loggers."""
    handler = TelegramHandler(chat_id=chat_id, level=logging.ERROR)
    for logger_name in (
        "gmail_inbox_bot.bot",
        "gmail_inbox_bot.actions",
        "gmail_inbox_bot.gmail_client",
        "gmail_inbox_bot.classifier",
    ):
        logging.getLogger(logger_name).addHandler(handler)
