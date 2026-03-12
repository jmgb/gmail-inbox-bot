# logger.py

import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo

# build_info removed — standalone service, no monolith dependency

# Timezone de Madrid (Europe/Madrid)
MADRID_TZ = ZoneInfo("Europe/Madrid")


class MadridFormatter(logging.Formatter):
    """Formatter que usa siempre la hora de Madrid (Europe/Madrid)."""

    def formatTime(self, record, datefmt=None):
        """Sobrescribe formatTime para usar timezone de Madrid."""
        dt = datetime.fromtimestamp(record.created, tz=MADRID_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.isoformat(timespec="seconds")


class BuildInfoFilter(logging.Filter):
    """Inyecta el build_id en cada registro de log (standalone: fixed value)."""

    def __init__(self) -> None:
        super().__init__()
        self._build_id = "standalone"

    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        record.build_id = self._build_id  # type: ignore[attr-defined]
        return True


def get_logger(name: str) -> logging.Logger:
    """Wrapper for internal logger access (avoid direct logging.getLogger usage elsewhere)."""
    return logging.getLogger(name)


def get_root_logger() -> logging.Logger:
    """Wrapper for root logger access (avoid direct logging.getLogger usage elsewhere)."""
    return logging.getLogger()


def set_external_logger_level(name: str, level: int) -> None:
    """Set level on external library logger via centralized helper."""
    logging.getLogger(name).setLevel(level)


def _handler_targets_path(handler: logging.Handler, path: str) -> bool:
    if not isinstance(handler, RotatingFileHandler):
        return False
    return os.path.abspath(handler.baseFilename) == os.path.abspath(path)


def setup_logger(
    name: str,
    log_file: str,
    debug_mode: bool = False,
    mirror_to_app_log: bool = False,
) -> logging.Logger:
    """Configura un logger con rotación de fichero y salida a consola."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    # Nivel global
    level_console = logging.DEBUG if debug_mode else logging.INFO
    level_file = logging.DEBUG

    # Formatter extendido con timezone de Madrid
    fmt = (
        "%(asctime)s %(name)s %(module)s:%(lineno)d "
        "[%(levelname)s] [build=%(build_id)s] %(message)s"
    )
    datefmt = "%Y-%m-%d %H:%M:%S"
    formatter = MadridFormatter(fmt, datefmt)

    # Handler consola
    ch = logging.StreamHandler()
    ch.setLevel(level_console)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Asegurar carpeta
    os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
    # RotatingFileHandler: 5 MB por fichero, 5 backups
    fh = RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(level_file)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    if mirror_to_app_log:
        app_log_path = "logs/app.log"
        if os.path.abspath(log_file) != os.path.abspath(app_log_path):
            if not any(_handler_targets_path(handler, app_log_path) for handler in logger.handlers):
                app_fh = RotatingFileHandler(
                    app_log_path,
                    maxBytes=5 * 1024 * 1024,
                    backupCount=5,
                    encoding="utf-8",
                )
                app_fh.setLevel(level_file)
                app_fh.setFormatter(formatter)
                logger.addHandler(app_fh)

    logger.setLevel(logging.DEBUG)  # Dejar que handlers filtren

    # Añadir filtro de build info para que el formatter tenga %(build_id)s
    logger.addFilter(BuildInfoFilter())

    # Configurar logger root en modo debug
    if debug_mode:
        root_logger = logging.getLogger()
        if not root_logger.handlers:  # Solo si no tiene handlers
            root_logger.setLevel(logging.DEBUG)

    # No propagar al root
    logger.propagate = False

    # Silenciar módulos verbosos (excepto en modo debug)
    if not debug_mode:
        for mod in [
            "httpx",
            "httpcore",
            "urllib3",
            "asyncio",
            "watchfiles",
            "uvicorn",
            "openai",
        ]:
            logging.getLogger(mod).setLevel(logging.WARNING)
        # También reducir verbosidad de access/error de uvicorn
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
        logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    else:
        # En modo debug, permitir más logs útiles y mostrar uvicorn
        logging.getLogger("openai").setLevel(logging.INFO)
        # Mantener algunos módulos ruidosos en WARNING, pero dejar uvicorn visible
        for mod in [
            "httpx",
            "httpcore",
            "urllib3",
            "asyncio",
            "watchfiles",
        ]:
            logging.getLogger(mod).setLevel(logging.WARNING)
        # Mostrar logs de servidor en consola
        logging.getLogger("uvicorn").setLevel(logging.INFO)
        logging.getLogger("uvicorn.access").setLevel(logging.INFO)
        logging.getLogger("uvicorn.error").setLevel(logging.INFO)

    return logger
