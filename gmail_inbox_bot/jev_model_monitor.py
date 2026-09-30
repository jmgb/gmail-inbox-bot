"""Report changes in Jev's resolved version without blocking classification."""

import fcntl
import re
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from tempfile import NamedTemporaryFile

from .logger import setup_logger

logger = setup_logger("gmail_inbox_bot.jev_model_monitor", "logs/app.log")
STATE_PATH = Path("logs/jev_model_version.txt")
_VERSION = re.compile(r"jev-\d+(?:\.\d+){1,2}")
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jev-version")


def _is_version(model: object) -> bool:
    return isinstance(model, str) and _VERSION.fullmatch(model) is not None


def _notify(message: str) -> None:
    from .telegram import enviar_mensaje_telegram

    enviar_mensaje_telegram(message, referencia="jev_version_change", nivel="ok")


def _check_version(model: object) -> None:
    """Persist the first version silently; claim each transition across processes."""
    if not _is_version(model):
        return
    temporary: Path | None = None
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with STATE_PATH.with_suffix(".lock").open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = STATE_PATH.read_text(encoding="utf-8").strip() if STATE_PATH.exists() else ""
            if previous == model:
                return
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=STATE_PATH.parent, delete=False
            ) as state:
                temporary = Path(state.name)
                state.write(f"{model}\n")
            temporary.replace(STATE_PATH)
            # Persist before dispatch: other processes and restarts cannot repeat it.
            if _is_version(previous):
                message = (
                    "✅ " + f"Jev cambió de {previous} a {model}.\n"
                    "jev-latest sigue activo; actualización automática."
                )
                logger.info(message)
                _notify(message)
    except Exception as exc:
        # No model-monitor failure may turn a valid Jev answer into an LLM fallback.
        logger.warning("Jev version monitor failed (%s)", type(exc).__name__)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def observe_jev_model(model: object) -> Future[None] | None:
    """Queue state checks and Telegram delivery outside the decision path."""
    if not _is_version(model):
        return None
    try:
        return _executor.submit(_check_version, model)
    except Exception as exc:
        logger.warning("Jev version observation failed (%s)", type(exc).__name__)
        return None
