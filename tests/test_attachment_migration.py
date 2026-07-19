import hashlib
from pathlib import Path

from gmail_inbox_bot.attachment_manifest import Manifest
from scripts.migrate_archive_layout import migrate_archive


def test_migrate_archive_creates_flat_attachment_folder_and_updates_manifest(tmp_path: Path):
    old_message = tmp_path / "jesus82c" / "2026-07" / "message-1"
    old_files = old_message / "files"
    old_files.mkdir(parents=True)
    old_eml = old_message / "message.eml"
    old_eml.write_bytes(b"eml")
    old_attachment = old_files / "1_invoice.pdf"
    old_attachment.write_bytes(b"pdf")
    manifest = Manifest(tmp_path / ".state.sqlite3")
    manifest.record_message(
        account="jesus82c@gmail.com",
        mailbox="jesus82c",
        message_id="message-1",
        thread_id="thread-1",
        subject="Invoice",
        sender="sender@example.com",
        internal_date="2026-07-19T10:00:00+00:00",
        label_ids=[],
        eml_path="jesus82c/2026-07/message-1/message.eml",
        eml_size=3,
        eml_sha256=hashlib.sha256(b"eml").hexdigest(),
        status="completed",
    )
    manifest.record_artifact(
        account="jesus82c@gmail.com",
        message_id="message-1",
        part_key="1",
        kind="pdf",
        filename="invoice.pdf",
        local_path="jesus82c/2026-07/message-1/files/1_invoice.pdf",
        mime_type="application/pdf",
        size_bytes=3,
        sha256=hashlib.sha256(b"pdf").hexdigest(),
    )

    migrate_archive(tmp_path, manifest)

    flat_attachment = tmp_path / "jesus82c" / "attachments" / "message-1_1_invoice.pdf"
    flat_eml = tmp_path / "jesus82c" / "messages" / "message-1.eml"
    assert flat_attachment.read_bytes() == b"pdf"
    assert flat_eml.read_bytes() == b"eml"
    assert manifest.db.execute("select local_path from artifacts").fetchone()[0] == str(
        flat_attachment.relative_to(tmp_path)
    )
    assert manifest.db.execute("select eml_path from messages").fetchone()[0] == str(
        flat_eml.relative_to(tmp_path)
    )
    manifest.close()
