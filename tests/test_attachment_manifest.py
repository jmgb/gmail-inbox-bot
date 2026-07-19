import csv
from pathlib import Path

from gmail_inbox_bot.attachment_manifest import Manifest


def test_manifest_round_trip_and_csv_preserves_delete_marker(tmp_path: Path):
    manifest = Manifest(tmp_path / ".state.sqlite3")
    manifest.record_message(
        account="jesus82c@gmail.com",
        mailbox="jesus82c",
        message_id="msg-1",
        thread_id="thread-1",
        subject="Invoice",
        sender="sender@example.com",
        internal_date="2026-07-19T10:00:00+00:00",
        label_ids=["INBOX"],
        gmail_size_estimate=100,
        eml_path="jesus82c/2026-07/message-1/message.eml",
        eml_size=100,
        eml_sha256="abc",
        status="completed",
    )
    manifest.record_artifact(
        account="jesus82c@gmail.com",
        message_id="msg-1",
        part_key="2",
        kind="pdf",
        filename="invoice.pdf",
        local_path="jesus82c/2026-07/message-1/files/2_invoice.pdf",
        mime_type="application/pdf",
        size_bytes=8,
        sha256="def",
        status="completed",
    )
    csv_path = tmp_path / "messages.csv"
    manifest.export_csv(csv_path, existing_csv=None)
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["borrar"] = "x"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)

    # A second export must retain the user's marker by message key.
    manifest.export_csv(csv_path, existing_csv=csv_path)
    content = csv_path.read_text(encoding="utf-8-sig")
    assert "msg-1" in content
    assert ",x\n" in content

    manifest.close()
