"""Registro best-effort de envíos a Telegram para el triage matinal.

Copia estructurada de cada aviso en el JSONL del host
(autofix-control-plane/logs/telegram/, ver docs/TRIAGE_TELEGRAM.md del
control plane). Un fallo aquí jamás debe impedir el envío real.

Este módulo corre tanto en el contenedor (TELEGRAM_ACTIVITY_LOG del compose
manda y el nombre del escritor no se usa) como en la WSL del usuario vía
scripts/download_invoice_emails.py (cron local); por eso el escritor del
fallback lleva sufijo -host y el campo `repo` va sin él.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

_WRITER = "gmail-inbox-bot-host"
_REPO = "gmail-inbox-bot"


def _destino() -> Path | None:
    env = os.environ.get("TELEGRAM_ACTIVITY_LOG")
    if env:
        return Path(env)
    for d in (
        Path("/home/ubuntu/autofix-control-plane/logs/telegram"),
        Path.home() / "ai_projects/autofix-control-plane/logs/telegram",
    ):
        if d.is_dir():
            return d / f"{_WRITER}.jsonl"
    return None


def _nivel(texto: str) -> str:
    if any(e in texto for e in ("❌", "🔴", "🚨", "⛔")):
        return "error"
    if "⚠" in texto:
        return "warning"
    return "ok"


def registrar(texto: str, referencia: str = "", nivel: str | None = None) -> None:
    try:
        destino = _destino()
        if destino is None:
            return
        recorte = texto.encode("utf-8")[:1000].decode("utf-8", errors="ignore")
        linea = json.dumps(
            {
                "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "repo": _REPO,
                "host": os.environ.get("TELEGRAM_ACTIVITY_HOST")
                or socket.gethostname().split(".")[0],
                "nivel": nivel or _nivel(texto),
                "referencia": referencia,
                "texto": recorte,
            },
            ensure_ascii=False,
        )
        with open(destino, "a", encoding="utf-8") as f:
            f.write(linea + "\n")
    except Exception:
        pass
