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
    assert is_invoice_candidate("Sua fatura está fechada", ["doc.pdf"])  # pt: una sola t
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
    ok = build_message("2026-08", downloaded=[{"mailbox": "jesus82c"}], skipped=[],
                       review=[1, 2], errors=[], folder="C:\\x")
    assert ok.startswith("✅") and "2026-08" in ok and "revisar: 2" in ok
    ko = build_message("2026-08", downloaded=[], skipped=[], review=[],
                       errors=[{"mailbox": "j", "message_id": "m", "error": "boom"}],
                       folder="C:\\x")
    assert ko.startswith("❌") and "boom" in ko


def _raw(subject: str, pdf_name: str | None) -> bytes:
    message = EmailMessage()
    message["From"] = "Hostinger <billing@hostinger.com>"
    message["To"] = "me@example.com"
    message["Subject"] = subject
    message.set_content("cuerpo")
    if pdf_name:
        message.add_attachment(b"%PDF-1.7 x", maintype="application",
                               subtype="pdf", filename=pdf_name)
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
    gmail = FakeGmail({
        "aaaa1111": _raw("Tu factura de agosto", "factura.pdf"),
        "bbbb2222": _raw("Fotos del finde", "fotos.pdf"),
    })
    mailbox = {"name": "jesus82c", "email": "jesus82c@gmail.com"}
    result = process_account(gmail=gmail, mailbox=mailbox, month="2026-08",
                             dest=tmp_path, force=False)
    assert len(result["downloaded"]) == 1 and not result["errors"]
    assert len(result["review"]) == 1 and result["review"][0]["subject"] == "Fotos del finde"
    saved = list((tmp_path / "jesus82c").glob("*.pdf"))
    assert len(saved) == 1 and saved[0].read_bytes().startswith(b"%PDF-")
    # idempotencia: segunda pasada no re-descarga
    again = process_account(gmail=gmail, mailbox=mailbox, month="2026-08",
                            dest=tmp_path, force=False)
    assert len(again["skipped"]) == 1 and not again["downloaded"]
