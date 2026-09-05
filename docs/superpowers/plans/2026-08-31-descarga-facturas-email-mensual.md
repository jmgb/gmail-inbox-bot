# Descarga mensual de facturas recibidas por email — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un cron el día 1 de cada mes que lea las dos cuentas Gmail conectadas (`jesus82c@gmail.com` y `miguelgutierrezbarquin@gmail.com`), localice los emails del mes anterior con facturas en PDF adjuntas y las guarde en la carpeta de facturas del escritorio de Windows, con índice CSV y aviso por Telegram.

**Architecture:** Un script standalone `scripts/download_invoice_emails.py` en este repo (que ya posee el OAuth de ambas cuentas), calcado al patrón de `scripts/download_attachments.py`: reutiliza `load_env`/`load_mailbox_configs`, `_build_gmail_client` (refresh tokens permanentes de `.env`), `iter_message_stubs` + `get_raw_message` (búsqueda Gmail paginada + RFC822 crudo), `extract_artifacts`/`safe_filename` (extracción segura de PDFs) y `enviar_mensaje_telegram`. Solo lectura sobre Gmail: no etiqueta, no archiva, no borra. Idempotente: un PDF ya descargado (no vacío) se salta salvo `--force`. Los emails con PDF que NO parecen factura se listan en `revisar.csv` para auditoría manual — nada se pierde en silencio.

**Tech Stack:** Python 3.13 + uv (entorno del repo), Gmail API vía `GmailClient` propio, pytest, cron de la máquina WSL local (única con acceso a `/mnt/c`).

**Decisiones tomadas (con el porqué):**

- **Vive en `gmail-inbox-bot`, no en `doctor`:** aquí están los tokens, el cliente Gmail probado (343 tests verdes) y el extractor MIME endurecido. Duplicarlo en doctor sería DRY violado; doctor solo recibe un puntero en su skill `doctor-invoices`.
- **Destino:** `/mnt/c/Users/USER/Desktop/Facturas Doctor/<YYYY-MM>/email/<mailbox>/` — la misma carpeta mensual que ya usa el cron de facturas de las tiendas, subcarpeta `email/` para separar recibidas de emitidas. Configurable con `--dest` / `DOCTOR_INVOICE_DESKTOP_DIR`.
- **Selección en dos fases:** (1) query Gmail `has:attachment filename:pdf after:<epoch> before:<epoch>` con épocas del mes anterior en `Europe/Madrid` (los epochs evitan la ambigüedad de zona de `after:YYYY/MM/DD`); (2) filtro local por palabras clave en asunto + nombre de fichero (`factura`, `invoice`, `receipt`, `recibo`, `facture`, `fattura`, `rechnung`, `billing`). Lo que trae PDF pero no casa va a `revisar.csv` (fecha, remitente, asunto), nunca al vacío.
- **Nombre de fichero determinista y sin colisiones:** `<YYYYMMDD>_<dominio-remitente>_<msgid8>_<nombre-original>.pdf`. El `msgid8` garantiza unicidad y hace el skip idempotente estable entre ejecuciones.
- **Cron a las 09:30 del día 1** (el de tiendas va a las 09:00), en esta máquina WSL. Si el PC está apagado, se relanza a mano: idempotente.
- **Telegram:** `enviar_mensaje_telegram` del repo (prefijo `[Gmail Bot]` en negrita lo pone él; primera línea con ✅/❌ como en el resto del ecosistema). Exit codes: 0 OK, 1 errores, 2 Telegram falló.
- **Gmail intocado:** el scope es `gmail.modify` pero este script solo hace GET. Prohibido añadir labels/trash aquí (regla del repo: nada destructivo sin confirmación).

**Límites conocidos (aceptados):**

- Facturas que llegan como **enlace** (sin PDF adjunto) no se descargan; si el asunto casa con las keywords aparecerán en `revisar.csv` (el filtro de candidatos se aplica a todos los emails con PDF; los sin adjunto no entran en la query). Ampliarlo sería scraping de portales — fuera de alcance.
- Un mismo PDF adjunto en dos emails se guarda dos veces (msgid distinto). Aceptable: la deduplicación la hace el humano al contabilizar.

---

## File Structure

- Create: `scripts/download_invoice_emails.py` — script completo (helpers puros + main), patrón de `scripts/download_attachments.py`.
- Create: `tests/test_download_invoice_emails.py` — tests de los helpers puros + flujo con `FakeGmailClient` (mismo estilo que `tests/test_download_attachments.py`).
- Modify: `README.md` — una línea en la sección `## Comandos`.
- Fuera del repo: entrada en el crontab local; puntero en `~/ai_projects/doctor/.claude/skills/doctor-invoices/SKILL.md` y `memory.md` de doctor (commit separado allí).

---

### Task 1: Helpers puros con TDD

**Files:**
- Create: `tests/test_download_invoice_emails.py`
- Create: `scripts/download_invoice_emails.py`

- [ ] **Step 1: Escribir los tests que fallan**

```python
"""Tests de scripts/download_invoice_emails.py (helpers puros y flujo)."""

import datetime as dt
import zoneinfo
from email.message import EmailMessage
from pathlib import Path

from scripts.download_invoice_emails import (
    build_message,
    gmail_query,
    is_invoice_candidate,
    month_bounds_epoch,
    pdf_filename,
    previous_month,
    process_account,
)

MADRID = zoneinfo.ZoneInfo("Europe/Madrid")


def test_previous_month_rolls_over_the_year():
    assert previous_month(dt.date(2026, 9, 1)) == "2026-08"
    assert previous_month(dt.date(2026, 1, 15)) == "2025-12"


def test_month_bounds_epoch_uses_madrid_midnights():
    start, end = month_bounds_epoch("2026-08")
    assert start == int(dt.datetime(2026, 8, 1, tzinfo=MADRID).timestamp())
    assert end == int(dt.datetime(2026, 9, 1, tzinfo=MADRID).timestamp())


def test_gmail_query_targets_pdf_attachments_in_month():
    q = gmail_query("2026-08")
    start, end = month_bounds_epoch("2026-08")
    assert q == f"has:attachment filename:pdf after:{start} before:{end}"


def test_is_invoice_candidate_matches_subject_or_filename_case_insensitive():
    assert is_invoice_candidate("Tu FACTURA de agosto", ["adjunto.pdf"])
    assert is_invoice_candidate("Payment confirmation", ["Invoice-2026-08.pdf"])
    assert is_invoice_candidate("Ihre Rechnung", ["doc.pdf"])
    assert not is_invoice_candidate("Fotos del viaje", ["fotos.pdf"])


def test_pdf_filename_is_deterministic_and_safe():
    name = pdf_filename(
        internal_date_iso="2026-08-14T09:30:00+00:00",
        sender="billing@hostinger.com",
        message_id="18f2a9c0deadbeef",
        original="Factura Agosto/2026 final.pdf",
    )
    assert name == "20260814_hostinger.com_18f2a9c0_Factura Agosto_2026 final.pdf"


def test_build_message_uses_status_icon_and_counts():
    ok = build_message(
        "2026-08",
        downloaded=[{"mailbox": "jesus82c"}],
        skipped=[],
        review=[1, 2],
        errors=[],
        folder="C:\\x",
    )
    assert ok.startswith("✅") and "2026-08" in ok and "revisar: 2" in ok
    ko = build_message(
        "2026-08",
        downloaded=[],
        skipped=[],
        review=[],
        errors=[{"mailbox": "j", "message_id": "m", "error": "boom"}],
        folder="C:\\x",
    )
    assert ko.startswith("❌") and "boom" in ko


def _raw(subject: str, pdf_name: str | None) -> bytes:
    message = EmailMessage()
    message["From"] = "Hostinger <billing@hostinger.com>"
    message["To"] = "me@example.com"
    message["Subject"] = subject
    message.set_content("cuerpo")
    if pdf_name:
        message.add_attachment(
            b"%PDF-1.7 x", maintype="application", subtype="pdf", filename=pdf_name
        )
    return message.as_bytes()


class FakeGmail:
    def __init__(self, messages: dict[str, bytes]):
        self._messages = messages

    def iter_message_stubs(self, *, query, include_spam_trash=False, page_size=500):
        for message_id in self._messages:
            yield {"id": message_id}

    def get_raw_message(self, message_id: str) -> dict:
        return {
            "id": message_id,
            "internalDate": "1786700000000",  # 2026-08-14 UTC aprox
            "raw_bytes": self._messages[message_id],
        }


def test_process_account_downloads_invoices_skips_existing_and_reports_review(tmp_path: Path):
    gmail = FakeGmail(
        {
            "aaaa1111": _raw("Tu factura de agosto", "factura.pdf"),
            "bbbb2222": _raw("Fotos del finde", "fotos.pdf"),
        }
    )
    mailbox = {"name": "jesus82c", "email": "jesus82c@gmail.com"}
    result = process_account(
        gmail=gmail, mailbox=mailbox, month="2026-08", dest=tmp_path, force=False
    )
    assert len(result["downloaded"]) == 1 and not result["errors"]
    assert len(result["review"]) == 1 and result["review"][0]["subject"] == "Fotos del finde"
    saved = list((tmp_path / "jesus82c").glob("*.pdf"))
    assert len(saved) == 1 and saved[0].read_bytes().startswith(b"%PDF-")
    # idempotencia: segunda pasada no re-descarga
    again = process_account(
        gmail=gmail, mailbox=mailbox, month="2026-08", dest=tmp_path, force=False
    )
    assert len(again["skipped"]) == 1 and not again["downloaded"]
```

- [ ] **Step 2: Ejecutar y verificar que fallan**

Run: `cd /home/ubuntu/ai_projects/gmail-inbox-bot && uv run pytest tests/test_download_invoice_emails.py -q`
Expected: FAIL/ERROR con `ModuleNotFoundError: scripts.download_invoice_emails` (nota: `scripts/` ya es paquete importable — `tests/test_download_attachments.py` importa igual).

- [ ] **Step 3: Escribir el script completo**

```python
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
DEFAULT_DEST = Path("/mnt/c/Users/USER/Desktop/Facturas Doctor")
KEYWORDS = ("factura", "invoice", "receipt", "recibo", "facture", "fattura", "rechnung", "billing")


# ---------- helpers puros ----------
def previous_month(today: dt.date) -> str:
    return (today.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")


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


def pdf_filename(*, internal_date_iso: str, sender: str, message_id: str, original: str) -> str:
    day = (internal_date_iso or "").replace("-", "")[:8] or "00000000"
    domain = (sender.rsplit("@", 1)[-1] or "desconocido").lower()
    return f"{day}_{safe_filename(domain)}_{message_id[:8]}_{safe_filename(original)}"


def build_message(month, *, downloaded, skipped, review, errors, folder) -> str:
    total = len(downloaded) + len(skipped)
    per_box: dict[str, int] = {}
    for entry in downloaded + skipped:
        per_box[entry["mailbox"]] = per_box.get(entry["mailbox"], 0) + 1
    detail = ", ".join(f"{k} {v}" for k, v in sorted(per_box.items())) or "ninguna"
    if errors:
        lines = [f"❌ Facturas email de {month}: {len(errors)} error(es), {total} OK ({detail})"]
        lines += [f"- {e['mailbox']} {e['message_id']}: {e['error']}" for e in errors[:10]]
    else:
        lines = [
            f"✅ Facturas email de {month}: {total} ({detail}); "
            f"nuevas {len(downloaded)}, ya existentes {len(skipped)}"
        ]
    if review:
        lines.append(f"Emails con PDF sin pinta de factura, a revisar: {len(review)} (revisar.csv)")
    lines.append(f"Carpeta: {folder}")
    return "\n".join(lines)


# ---------- flujo por cuenta ----------
def process_account(*, gmail, mailbox: dict, month: str, dest: Path, force: bool) -> dict:
    name = mailbox.get("name", mailbox["email"])
    out_dir = dest / safe_filename(name)
    downloaded, skipped, review, errors = [], [], [], []
    for stub in gmail.iter_message_stubs(query=gmail_query(month), page_size=500):
        message_id = stub["id"]
        try:
            raw = gmail.get_raw_message(message_id)
            parsed = BytesParser(policy=policy.default).parsebytes(raw["raw_bytes"])
            subject = str(parsed.get("Subject", ""))
            sender = parseaddr(str(parsed.get("From", "")))[1]
            stamp = int(raw.get("internalDate", "0")) // 1000
            date_iso = dt.datetime.fromtimestamp(stamp, tz=dt.timezone.utc).isoformat()
            pdf_names = [
                p.get_filename() or ""
                for p in parsed.walk()
                if p.get_content_type().lower() == "application/pdf"
            ]
            if not is_invoice_candidate(subject, pdf_names):
                review.append(
                    {"mailbox": name, "date": date_iso[:10], "sender": sender, "subject": subject}
                )
                continue
            artifacts = extract_artifacts(
                raw["raw_bytes"], out_dir, filename_prefix=".tmp_extract_"
            )
            for artifact in artifacts:
                if artifact.kind != "pdf":
                    artifact.path.unlink(missing_ok=True)
                    continue
                final = out_dir / pdf_filename(
                    internal_date_iso=date_iso,
                    sender=sender,
                    message_id=message_id,
                    original=artifact.filename,
                )
                entry = {
                    "mailbox": name,
                    "date": date_iso[:10],
                    "sender": sender,
                    "subject": subject,
                    "message_id": message_id,
                    "file": final.name,
                    "size": artifact.size_bytes,
                    "sha256": artifact.sha256,
                }
                if final.exists() and final.stat().st_size > 0 and not force:
                    artifact.path.unlink(missing_ok=True)
                    skipped.append(entry)
                else:
                    artifact.path.replace(final)
                    downloaded.append(entry)
        except Exception as exc:  # noqa: BLE001 — un email malo no para la pasada
            errors.append(
                {"mailbox": name, "message_id": message_id, "error": f"{type(exc).__name__}: {exc}"}
            )
            print(f"[{name}] {message_id}: {exc}", file=sys.stderr)
    return {"downloaded": downloaded, "skipped": skipped, "review": review, "errors": errors}


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
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--month", help="YYYY-MM (por defecto, el mes anterior)")
    parser.add_argument(
        "--mailbox", action="append", help="nombre o email; repetible (por defecto, todas)"
    )
    parser.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    parser.add_argument("--dry-run", action="store_true", help="lista sin descargar")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--notify", action="store_true", help="aviso Telegram (cron)")
    args = parser.parse_args()
    env = load_env()
    month = args.month or previous_month(dt.date.today())
    dest = args.dest / month / "email"
    boxes = load_mailbox_configs()
    if args.mailbox:
        boxes = [b for b in boxes if {b.get("name"), b.get("email")} & set(args.mailbox)]
        if not boxes:
            print("ningún mailbox coincide", file=sys.stderr)
            return 2

    totals = {"downloaded": [], "skipped": [], "review": [], "errors": []}
    for mailbox in boxes:
        gmail = _build_gmail_client(env, mailbox, request_rate_per_second=3.0, request_retries=5)
        try:
            if args.dry_run:
                count = sum(1 for _ in gmail.iter_message_stubs(query=gmail_query(month)))
                print(
                    f"[{mailbox['name']}] {count} emails con PDF en {month} "
                    f"(query: {gmail_query(month)})"
                )
                continue
            result = process_account(
                gmail=gmail, mailbox=mailbox, month=month, dest=dest, force=args.force
            )
            for key in totals:
                totals[key] += result[key]
        finally:
            gmail._http.close()
    if args.dry_run:
        return 0

    write_csv(
        dest / "indice_email.csv",
        totals["downloaded"] + totals["skipped"],
        ["mailbox", "date", "sender", "subject", "message_id", "file", "size", "sha256"],
    )
    if totals["review"]:
        write_csv(dest / "revisar.csv", totals["review"], ["mailbox", "date", "sender", "subject"])
    message = build_message(
        month,
        downloaded=totals["downloaded"],
        skipped=totals["skipped"],
        review=totals["review"],
        errors=totals["errors"],
        folder=windows_path(dest),
    )
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
```

- [ ] **Step 4: Ejecutar los tests y verificar que pasan**

Run: `uv run pytest tests/test_download_invoice_emails.py -q`
Expected: `7 passed`

- [ ] **Step 5: Lint y suite completa**

Run: `uv run ruff check scripts/download_invoice_emails.py tests/test_download_invoice_emails.py && uv run pytest -q`
Expected: `All checks passed!` y `350 passed` (343 previos + 7 nuevos).

- [ ] **Step 6: Commit**

```bash
git add scripts/download_invoice_emails.py tests/test_download_invoice_emails.py
git commit -m "feat: descarga mensual de facturas PDF recibidas por email"
```

---

### Task 2: Validación real con agosto 2026

- [ ] **Step 1: Dry-run en ambas cuentas**

Run: `uv run python scripts/download_invoice_emails.py --month 2026-08 --dry-run`
Expected: una línea por cuenta con el nº de emails con PDF del mes; sin errores de auth (si un refresh token fallara, revisar `GOOGLE_REFRESH_TOKEN_*` en `.env`).

- [ ] **Step 2: Descarga real a la carpeta del escritorio**

Run: `uv run python scripts/download_invoice_emails.py --month 2026-08`
Expected: exit 0; PDFs en `C:\Users\USER\Desktop\Facturas Doctor\2026-08\email\<mailbox>\`, `indice_email.csv` y (si aplica) `revisar.csv`. Comprobar que cada PDF abre (`file *.pdf` → `PDF document`) y revisar `revisar.csv` por si el filtro de keywords deja fuera algún proveedor real — si falta alguno, añadir su keyword a `KEYWORDS` con su test en Task 1 y repetir.

- [ ] **Step 3: Verificar idempotencia**

Run: `uv run python scripts/download_invoice_emails.py --month 2026-08`
Expected: `nuevas 0, ya existentes N` en el resumen.

---

### Task 3: Cron + README

- [ ] **Step 1: Añadir la entrada al crontab local**

```bash
(crontab -l; printf '\n# Facturas PDF recibidas por email, día 1 a las 09:30 (gmail-inbox-bot)\n30 9 1 * * cd /home/ubuntu/ai_projects/gmail-inbox-bot && /home/ubuntu/.local/bin/uv run python scripts/download_invoice_emails.py --notify >> /home/ubuntu/ai_projects/gmail-inbox-bot/logs/monthly_invoice_emails.log 2>&1\n') | crontab -
crontab -l | tail -2
```

Expected: la línea aparece; `systemctl is-active cron` → `active`. (09:30 para no solapar con el cron de facturas de tiendas de las 09:00 del repo doctor.)

- [ ] **Step 2: README — sección Comandos**

En `README.md`, dentro del bloque de código de `## Comandos`, añadir tras la línea de `--dry-run`:

```bash
uv run python scripts/download_invoice_emails.py  # facturas PDF del mes anterior al escritorio (cron día 1, 09:30)
```

- [ ] **Step 3: Commit**

```bash
git add README.md docs/superpowers/plans/2026-08-31-descarga-facturas-email-mensual.md
git commit -m "docs: comando y plan de la descarga mensual de facturas por email"
```

---

### Task 4: Puntero en el repo doctor

**Files:**
- Modify: `~/ai_projects/doctor/.claude/skills/doctor-invoices/SKILL.md` (sección Cron)
- Modify: `~/ai_projects/doctor/memory.md` (nota breve)

- [ ] **Step 1: Ampliar la skill doctor-invoices**

Añadir al final de la sección «## Cron (máquina local WSL, día 1 a las 09:00)»:

```markdown
Existe un segundo cron hermano a las **09:30** que descarga las **facturas recibidas por email**
(PDF adjuntos del mes anterior en `jesus82c@gmail.com` y `miguelgutierrezbarquin@gmail.com`) a
`<dest>/<YYYY-MM>/email/<cuenta>/` con `indice_email.csv` y `revisar.csv`. Vive en el repo
`~/ai_projects/gmail-inbox-bot` (`scripts/download_invoice_emails.py`, plan en
`docs/superpowers/plans/2026-08-31-descarga-facturas-email-mensual.md`) porque ahí están el OAuth
y el cliente Gmail. Log: `gmail-inbox-bot/logs/monthly_invoice_emails.log`.
```

- [ ] **Step 2: Nota en memory.md de doctor**

```markdown
## Facturas recibidas por email (31 ago 2026)

- Cron hermano del de tiendas: día 1 a las 09:30, repo `~/ai_projects/gmail-inbox-bot`
  (`scripts/download_invoice_emails.py`), reutiliza su OAuth (tokens permanentes en `.env`,
  `GOOGLE_REFRESH_TOKEN_JESUS82C` / `_MIGUELGUTIERREZBARQUIN`) y su `GmailClient`. Solo lectura
  sobre Gmail. Destino `Facturas Doctor/<YYYY-MM>/email/<cuenta>/`; los emails con PDF que no
  casan con las keywords van a `revisar.csv`, nunca se descartan en silencio.
```

- [ ] **Step 3: Commit en doctor**

```bash
cd /home/ubuntu/ai_projects/doctor
git add .claude/skills/doctor-invoices/SKILL.md memory.md
git commit -m "docs(facturas): puntero al cron de facturas recibidas por email (gmail-inbox-bot)"
```

---

## Self-review

- Cobertura: cron día 1 ✔ (Task 3), dos cuentas ✔ (configs YAML ya existentes, sin `--mailbox` procesa todas), reutiliza conexiones ✔ (`_build_gmail_client` + tokens `.env`), plan .md antes de implementar ✔ (este documento), descarga «todas las facturas» ✔ con red de seguridad `revisar.csv` para las que el filtro no reconozca.
- Sin placeholders: todo el código está inline.
- Consistencia de tipos: `process_account` devuelve `dict[str, list]` y `main` agrega con las mismas claves; `pdf_filename` usa kwargs idénticos en test e implementación.

---

## Addendum (31 ago 2026, tras feedback del usuario): dirección contable

- **Las recibidas son gastos y las enviadas por el titular son ingresos.** La búsqueda de Gmail ya
  cubría ambas (sin `in:` recorre All Mail, enviados incluidos); ahora `classify_direction()` separa
  por label `SENT` o remitente = titular, y los PDFs van a `email/<cuenta>/gastos/` o
  `email/<cuenta>/ingresos/`, con columna `tipo` en `indice_email.csv` y `revisar.csv`.
- **Los avisos de pedido de las tiendas propias se omiten** (`OWN_STORE_DOMAINS`, los 6 dominios
  doctor): adjuntan la factura de la venta, pero esa ya la descarga el cron de tiendas y aquí se
  clasificaría mal (como gasto). El resumen los cuenta («Emails de tiendas propias omitidos: N»).
  Detectado en la validación: los avisos «Novo pedido» de PT adjuntaban `Fatura-TEST-7/8.pdf` (QA).
- Validado con agosto: 13 gastos + 1 ingreso (la factura 340 «Aquisgran», enviada a mano — el hueco
  340 de la serie ES), 2 emails de tienda omitidos, 4 en revisar.csv.

## Addendum 2 (31 ago 2026): carpeta mensual definitiva

Por decisión del usuario, la estructura pasa a `C:\Users\USER\Desktop\Facturas\<Mes_YYYY>\gastos\`
e `ingresos\` (`month_folder()`: «Agosto_2026», mes en español). Sin subcarpeta por cuenta: las dos
cuentas comparten carpetas y la cuenta viaja como columna en `indice_email.csv`/`revisar.csv`, que
viven en la raíz del mes. El cron de tiendas del repo doctor escribe sus facturas (ingresos) en el
mismo `<Mes_YYYY>/ingresos/`. La carpeta anterior `Facturas Doctor/2026-08/` se eliminó y agosto se
regeneró completo en la nueva ruta (ambos crons son idempotentes).

## Addendum 3 (31 ago 2026): cron diario autocurativo

Los dos crons dejan de depender del día 1 exacto (si el PC/WSL estaba apagado, nadie se enteraba):
ahora corren **a diario** (09:00 tiendas, 09:30 email) con `--only-if-missing`. Una pasada sin
errores escribe `<Mes_YYYY>/.ok-tiendas` / `.ok-email`; mientras el marcador exista, el cron sale en
silencio sin tocar Gmail ni WordPress. Sin marcador (mes nuevo, PC apagado el día 1, o pasada con
errores) ejecuta la descarga completa y notifica. Un fallo persistente re-notifica ❌ una vez al día
hasta arreglarse. Validado: primera pasada escribe el marcador, la segunda responde «ya completado».
