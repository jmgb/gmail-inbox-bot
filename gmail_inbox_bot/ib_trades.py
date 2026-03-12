"""Interactive Brokers trade parser, Telegram notifier, and Sheets logger."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .sheets import SheetsClient
from .telegram import enviar_mensaje_telegram

log = logging.getLogger("gmail_inbox_bot.ib_trades")


@dataclass
class Trade:
    """Parsed trade from an IB email subject."""

    side: str  # BUY or SOLD
    quantity: int
    ticker: str
    price: float
    account: str
    timestamp: str  # ISO-8601 in Europe/Madrid


# Pattern: SOLD 1,511 VEEA @ 0.5722 (UXXX55709)
# Pattern: BOT 500 AAPL @ 182.50 (UXXX55709)  — IB uses "BOT" for buys
_TRADE_RE = re.compile(
    r"(?P<side>SOLD|BOT|BUY|BOUGHT)\s+"
    r"(?P<qty>[\d,]+)\s+"
    r"(?P<ticker>[A-Z0-9.]+)\s+"
    r"@\s*(?P<price>[\d,.]+)"
    r"(?:\s*\((?P<account>[^)]+)\))?"
)


def parse_trade(subject: str) -> Trade | None:
    """Parse an IB trade email subject into a Trade object."""
    m = _TRADE_RE.search(subject)
    if not m:
        return None

    side_raw = m.group("side").upper()
    side = "BUY" if side_raw in ("BOT", "BUY", "BOUGHT") else "SOLD"

    qty_str = m.group("qty").replace(",", "")
    price_str = m.group("price").replace(",", "")

    now_madrid = datetime.now(ZoneInfo("Europe/Madrid"))

    return Trade(
        side=side,
        quantity=int(qty_str),
        ticker=m.group("ticker"),
        price=float(price_str),
        account=m.group("account") or "",
        timestamp=now_madrid.isoformat(timespec="seconds"),
    )


def notify_trade(trade: Trade, mailbox: str) -> None:
    """Send a formatted Telegram notification for a trade."""
    emoji = "\U0001f7e2" if trade.side == "BUY" else "\U0001f534"
    total = trade.quantity * trade.price

    lines = [
        f"{emoji} <b>{trade.side} {trade.ticker}</b>",
        f"<b>Cantidad:</b> {trade.quantity:,}",
        f"<b>Precio:</b> ${trade.price:.4f}",
        f"<b>Total:</b> ${total:,.2f}",
    ]
    if trade.account:
        lines.append(f"<b>Cuenta:</b> {trade.account}")
    lines.append(f"<b>Buzón:</b> {mailbox}")
    lines.append(f"<b>Hora:</b> {trade.timestamp}")

    enviar_mensaje_telegram("\n".join(lines), referencia="ib_trade")


def _build_trade_row(trade: Trade) -> list:
    """Build a row matching the Resumen tab structure.

    Columns: A=ticker, B=acciones venta, C=precio venta, D=comision,
             E=(empty), F=acciones compra, G=precio compra, H=comision
    """
    if trade.side == "SOLD":
        return [trade.ticker, trade.quantity, trade.price, "", "", "", "", ""]
    else:  # BUY
        return [trade.ticker, "", "", "", "", trade.quantity, trade.price, ""]


def record_trade(trade: Trade, sheets: SheetsClient | None, *, sheet: str = "Resumen") -> None:
    """Insert a trade row into Google Sheets above the first existing trade row.

    Finds the first non-empty row after row 42 in the Resumen tab,
    inserts a new row just above it, and writes the trade data.
    """
    if not sheets:
        return
    try:
        insert_at = sheets.find_insert_row(sheet=sheet, search_from=43)
        row = _build_trade_row(trade)
        sheets.insert_row_at(insert_at, row, sheet=sheet)
    except Exception:
        log.exception("Failed to write trade to Sheets")
