#!/usr/bin/env python3
"""Descarga las facturas en PDF recibidas por email durante un mes en las cuentas Gmail
conectadas y las deja en la carpeta de facturas del escritorio de Windows (WSL2).

  uv run python scripts/download_invoice_emails.py                    # mes anterior, 2 cuentas
  uv run python scripts/download_invoice_emails.py --month 2026-08 --dry-run
  uv run python scripts/download_invoice_emails.py --notify           # cron del día 1

Solo lectura sobre Gmail (GET): no etiqueta, no archiva, no borra. Idempotente: un PDF ya
descargado (no vacío) se salta salvo --force. Los emails con PDF que no parecen factura se
anotan en revisar.csv en vez de descartarse en silencio.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
import zoneinfo
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gmail_inbox_bot.attachment_archive import extract_artifacts, safe_filename  # noqa: E402
from gmail_inbox_bot.bot import _build_gmail_client  # noqa: E402
from gmail_inbox_bot.config import load_env, load_mailbox_configs  # noqa: E402
from gmail_inbox_bot.telegram import enviar_mensaje_telegram  # noqa: E402

MADRID = zoneinfo.ZoneInfo("Europe/Madrid")
DEFAULT_DEST = Path("/mnt/c/Users/USER/Desktop/Facturas")
KEYWORDS = ("factura", "fatura", "invoice", "receipt", "recibo", "facture",
            "fattura", "rechnung", "billing")
# Los avisos de pedido de las tiendas propias adjuntan su factura, pero esas ya las descarga el
# cron de tiendas del repo doctor (y aquí se clasificarían mal, como gasto). Se omiten y se cuentan.
OWN_STORE_DOMAINS = ("drcornudo.com", "drcorno.com", "drcuckold.net",
                     "drcornuto.com", "drcocu.com", "drcuckold.de")


# ---------- helpers puros ----------
def previous_month(today: dt.date) -> str:
    return (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")


MESES_ES = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
            "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")


def month_folder(month: str) -> str:
    """Carpeta mensual legible: '2026-08' → 'Agosto_2026'."""
    year, num = month.split("-")
    return f"{MESES_ES[int(num) - 1]}_{year}"


def month_bounds_epoch(month: str) -> tuple[int, int]:
    start = dt.datetime.strptime(month, "%Y-%m").replace(tzinfo=MADRID)
    end_date = (start.date().replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    end = dt.datetime(end_date.year, end_date.month, 1, tzinfo=MADRID)
    return int(start.timestamp()), int(end.timestamp())


def gmail_query(month: str) -> str:
    start, end = month_bounds_epoch(month)
    return f"has:attachment filename:pdf after:{start} before:{end}"


def is_invoice_candidate(subject: str, filenames: list[str]) -> bool:
    haystack = " ".join([subject or "", *filenames]).lower()
    return any(keyword in haystack for keyword in KEYWORDS)


def classify_direction(*, sender: str, labels: list[str], me: str) -> str:
    """Enviada por el titular → factura emitida (ingresos); recibida → gasto."""
    if "SENT" in labels or (sender or "").lower() == (me or "").lower():
        return "ingresos"
    return "gastos"


def pdf_filename(*, internal_date_iso: str, sender: str, message_id: str, original: str) -> str:
    day = (internal_date_iso or "").replace("-", "")[:8] or "00000000"
    domain = (sender.rsplit("@", 1)[-1] or "desconocido").lower()
    return f"{day}_{safe_filename(domain)}_{message_id[:8]}_{safe_filename(original)}"


def build_message(month, *, downloaded, skipped, review, errors, folder) -> str:
    total = len(downloaded) + len(skipped)
    per_key: dict[str, int] = {}
    for entry in downloaded + skipped:
        for key in (entry.get("tipo", "?"), entry["mailbox"]):
            per_key[key] = per_key.get(key, 0) + 1
    detail = ", ".join(f"{k} {v}" for k, v in sorted(per_key.items())) or "ninguna"
    if errors:
        lines = [f"❌ Facturas email de {month}: {len(errors)} error(es), {total} OK ({detail})"]
        lines += [f"- {e['mailbox']} {e['message_id']}: {e['error']}" for e in errors[:10]]
    else:
        lines = [f"✅ Facturas email de {month}: {total} ({detail}); "
                 f"nuevas {len(downloaded)}, ya existentes {len(skipped)}"]
    if review:
        lines.append(f"Emails con PDF sin pinta de factura, a revisar: {len(review)} (revisar.csv)")
    lines.append(f"Carpeta: {folder}")
    return "\n".join(lines)


# ---------- flujo por cuenta ----------
def process_account(*, gmail, mailbox: dict, month: str, dest: Path, force: bool) -> dict:
    name = mailbox.get("name", mailbox["email"])
    out_dir = Path(dest)  # compartida entre cuentas: la cuenta va como columna en el índice
    downloaded, skipped, review, own_store, errors = [], [], [], [], []
    for stub in gmail.iter_message_stubs(query=gmail_query(month), page_size=500):
        message_id = stub["id"]
        try:
            raw = gmail.get_raw_message(message_id)
            parsed = BytesParser(policy=policy.default).parsebytes(raw["raw_bytes"])
            subject = str(parsed.get("Subject", ""))
            sender = parseaddr(str(parsed.get("From", "")))[1]
            stamp = int(raw.get("internalDate", "0")) // 1000
            date_iso = dt.datetime.fromtimestamp(stamp, tz=dt.timezone.utc).isoformat()
            pdf_names = [p.get_filename() or "" for p in parsed.walk()
                         if p.get_content_type().lower() == "application/pdf"]
            if sender.rsplit("@", 1)[-1].lower() in OWN_STORE_DOMAINS:
                own_store.append({"mailbox": name, "sender": sender, "subject": subject})
                continue
            tipo = classify_direction(sender=sender, labels=raw.get("labelIds") or [],
                                      me=mailbox["email"])
            if not is_invoice_candidate(subject, pdf_names):
                review.append({"mailbox": name, "tipo": tipo, "date": date_iso[:10],
                               "sender": sender, "subject": subject})
                continue
            artifacts = extract_artifacts(raw["raw_bytes"], out_dir,
                                          filename_prefix=".tmp_extract_")
            for artifact in artifacts:
                if artifact.kind != "pdf":
                    artifact.path.unlink(missing_ok=True)
                    continue
                final = out_dir / tipo / pdf_filename(internal_date_iso=date_iso, sender=sender,
                                                      message_id=message_id,
                                                      original=artifact.filename)
                final.parent.mkdir(parents=True, exist_ok=True)
                entry = {"mailbox": name, "tipo": tipo, "date": date_iso[:10], "sender": sender,
                         "subject": subject, "message_id": message_id,
                         "file": final.name, "size": artifact.size_bytes,
                         "sha256": artifact.sha256}
                if final.exists() and final.stat().st_size > 0 and not force:
                    artifact.path.unlink(missing_ok=True)
                    skipped.append(entry)
                else:
                    artifact.path.replace(final)
                    downloaded.append(entry)
        except Exception as exc:  # noqa: BLE001 — un email malo no para la pasada
            errors.append({"mailbox": name, "message_id": message_id,
                           "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{name}] {message_id}: {exc}", file=sys.stderr)
    return {"downloaded": downloaded, "skipped": skipped, "review": review,
            "own_store": own_store, "errors": errors}


# ---------- E/S ----------
def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def windows_path(path: Path) -> str:
    text = str(path)
    if text.startswith("/mnt/") and len(text) > 6:
        return f"{text[5].upper()}:" + text[6:].replace("/", "\\")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--month", help="YYYY-MM (por defecto, el mes anterior)")
    parser.add_argument("--mailbox", action="append",
                        help="nombre o email; repetible (por defecto, todas)")
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--dry-run", action="store_true", help="lista sin descargar")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--notify", action="store_true", help="aviso Telegram (cron)")
    args = parser.parse_args()
    env = load_env()
    month = args.month or previous_month(dt.date.today())
    dest = args.dest / month_folder(month)
    boxes = load_mailbox_configs()
    if args.mailbox:
        boxes = [b for b in boxes
                 if {b.get("name"), b.get("email")} & set(args.mailbox)]
        if not boxes:
            print("ningún mailbox coincide", file=sys.stderr)
            return 2

    totals = {"downloaded": [], "skipped": [], "review": [], "own_store": [], "errors": []}
    for mailbox in boxes:
        gmail = _build_gmail_client(env, mailbox, request_rate_per_second=3.0,
                                    request_retries=5)
        try:
            if args.dry_run:
                count = sum(1 for _ in gmail.iter_message_stubs(query=gmail_query(month)))
                print(f"[{mailbox['name']}] {count} emails con PDF en {month} "
                      f"(query: {gmail_query(month)})")
                continue
            result = process_account(gmail=gmail, mailbox=mailbox, month=month,
                                     dest=dest, force=args.force)
            for key in totals:
                totals[key] += result[key]
        finally:
            gmail._http.close()
    if args.dry_run:
        return 0

    write_csv(dest / "indice_email.csv", totals["downloaded"] + totals["skipped"],
              ["mailbox", "tipo", "date", "sender", "subject", "message_id", "file", "size",
               "sha256"])
    if totals["review"]:
        write_csv(dest / "revisar.csv", totals["review"],
                  ["mailbox", "tipo", "date", "sender", "subject"])
    message = build_message(month, downloaded=totals["downloaded"], skipped=totals["skipped"],
                            review=totals["review"], errors=totals["errors"],
                            folder=windows_path(dest))
    if totals["own_store"]:
        omitted = len(totals["own_store"])
        message += f"\nEmails de tiendas propias omitidos (los cubre el cron de tiendas): {omitted}"
    print(message)
    if args.notify:
        try:
            enviar_mensaje_telegram(message, referencia="facturas-email")
        except Exception as exc:  # noqa: BLE001
            print(f"telegram: {exc}", file=sys.stderr)
            return 2
    return 1 if totals["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
