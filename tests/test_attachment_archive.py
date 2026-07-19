from email.message import EmailMessage

from gmail_inbox_bot.attachment_archive import (
    decode_gmail_raw,
    extract_artifacts,
    safe_filename,
)


def _message_with_files() -> bytes:
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "me@example.com"
    msg["Subject"] = "Pilot"
    msg.set_content("Body")
    msg.add_attachment(b"%PDF-1.7", maintype="application", subtype="pdf", filename="doc.pdf")
    msg.add_attachment(
        b"\x89PNG\r\n",
        maintype="image",
        subtype="png",
        cid="<logo@example.com>",
        disposition="inline",
    )
    return msg.as_bytes()


def test_extract_artifacts_includes_pdf_and_inline_image(tmp_path):
    artifacts = extract_artifacts(_message_with_files(), tmp_path)

    assert [artifact.kind for artifact in artifacts] == ["pdf", "inline_image"]
    assert artifacts[0].filename == "doc.pdf"
    assert artifacts[1].mime_type == "image/png"
    assert artifacts[0].path.exists()
    assert artifacts[1].path.exists()


def test_inline_image_without_filename_gets_stable_safe_name(tmp_path):
    msg = EmailMessage()
    msg.set_content("Body")
    msg.add_attachment(b"GIF89a", maintype="image", subtype="gif", disposition="inline")

    artifacts = extract_artifacts(msg.as_bytes(), tmp_path)

    assert len(artifacts) == 1
    assert artifacts[0].filename.startswith("inline_")
    assert artifacts[0].filename.endswith(".gif")


def test_safe_filename_prevents_path_traversal_and_controls():
    assert safe_filename("../\x00secret/report.pdf") == "secret_report.pdf"


def test_decode_gmail_raw_accepts_unpadded_base64url():
    assert decode_gmail_raw("SGVsbG8") == b"Hello"
