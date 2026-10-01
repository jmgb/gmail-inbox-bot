"""TelegramHandler — logging handler that sends ERROR+ records to Telegram."""

from __future__ import annotations

import logging
import traceback

from .telegram import enviar_mensaje_telegram


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
            # Sin escapar aquí: enviar_mensaje_telegram ya escapa el texto entero. Escapar dos
            # veces convertía "De: Foo <a@b.com>" en "&lt;a@b.com&gt;" literal en el aviso.
            text = record.getMessage()
            emoji = "\U0001f6a8" if record.levelno == logging.ERROR else "\U0001f4a5"

            if func:
                msg = f"{emoji} <b>[{module}:{func}]</b> {text}"
            else:
                msg = f"{emoji} <b>[{module}]</b> {text}"

            if record.exc_info and record.exc_info[1]:
                exc_type = type(record.exc_info[1]).__name__
                exc_msg = str(record.exc_info[1])
                tb_lines = traceback.format_tb(record.exc_info[2])
                tb_short = "".join(tb_lines[-3:]).strip()
                msg += f"\n\n<b>Exception:</b> {exc_type}: {exc_msg}"
                if tb_short:
                    msg += f"\n<pre>{tb_short}</pre>"

            enviar_mensaje_telegram(msg, self.chat_id, referencia="telegram_logger")
        except Exception:
            pass  # never break the app because of notifications


def setup_telegram_logging(chat_id: str | None = None) -> None:
    """Attach TelegramHandler to gmail_inbox_bot loggers (idempotente).

    Incluye ``gmail_inbox_bot.app`` porque ahí se loguea la muerte de los daemon threads
    ("Bot thread crashed"), que es justo el fallo que nadie ve. Se llama tanto desde el
    arranque de FastAPI como desde ``bot.run()``, así que se evita enganchar dos veces:
    dos handlers = cada error notificado dos veces por Telegram.
    """
    handler = TelegramHandler(chat_id=chat_id, level=logging.ERROR)
    for logger_name in (
        "gmail_inbox_bot.app",
        "gmail_inbox_bot.bot",
        "gmail_inbox_bot.actions",
        "gmail_inbox_bot.gmail_client",
        "gmail_inbox_bot.classifier",
    ):
        logger = logging.getLogger(logger_name)
        if any(isinstance(h, TelegramHandler) for h in logger.handlers):
            continue
        logger.addHandler(handler)
