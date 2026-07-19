"""MIME extraction and safe local storage for the Gmail archive pilot."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import unicodedata
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from pathlib import Path


@dataclass(frozen=True)
class ExtractedArtifact:
    part_key: str
    kind: str
    filename: str
    path: Path
    mime_type: str
    disposition: str
    content_id: str
    size_bytes: int
    sha256: str


def decode_gmail_raw(raw: str) -> bytes:
    """Decode Gmail's base64url payload, including omitted padding."""
    import base64

    if not isinstance(raw, str) or not raw:
        raise ValueError("Gmail raw payload is empty")
    padded = raw + "=" * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode(padded)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid Gmail raw base64url payload") from exc


def safe_filename(filename: str, *, max_length: int = 120) -> str:
    """Return a deterministic basename that cannot escape the output directory."""
    normalised = unicodedata.normalize("NFC", filename or "")
    chunks: list[str] = []
    for chunk in re.split(r"[/\\]", normalised):
        if chunk in {"", ".", ".."}:
            continue
        chunk = re.sub(r"[\x00-\x1f\x7f]", "", chunk)
        if chunk:
            chunks.append(chunk)
    result = re.sub(r"\s+", " ", "_".join(chunks)).strip(" .") or "unnamed"
    if len(result) <= max_length:
        return result
    stem, extension = os.path.splitext(result)
    keep = max(1, max_length - len(extension))
    return f"{stem[:keep]}{extension}"


def _atomic_write(path: Path, data: bytes) -> tuple[int, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.part")
    temporary.write_bytes(data)
    with temporary.open("rb") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return len(data), hashlib.sha256(data).hexdigest()


def _artifact_kind(mime_type: str, disposition: str, content_id: str) -> str | None:
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type.startswith("image/") and (disposition == "inline" or content_id):
        return "inline_image"
    if disposition == "attachment" or mime_type.startswith("image/"):
        return "attachment"
    return None


def _payload_bytes(part) -> bytes:
    payload = part.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload
    if part.get_content_type() == "message/rfc822":
        nested = part.get_payload()
        if isinstance(nested, list):
            return b"\n".join(item.as_bytes(policy=policy.default) for item in nested)
    raise ValueError("MIME part has no decodable payload")


def extract_artifacts(
    raw_bytes: bytes,
    output_dir: Path,
    *,
    filename_prefix: str = "",
) -> list[ExtractedArtifact]:
    """Extract attachments, inline images, and PDFs from one RFC822 message."""
    message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
    artifacts: list[ExtractedArtifact] = []
    part_number = 0
    for part in message.walk():
        if part.is_multipart():
            continue
        part_number += 1
        mime_type = part.get_content_type().lower()
        disposition = (part.get_content_disposition() or "").lower()
        content_id = part.get("Content-ID", "").strip()
        filename = part.get_filename() or ""
        kind = _artifact_kind(mime_type, disposition, content_id)
        if kind is None:
            continue
        extension = mimetypes.guess_extension(mime_type) or ".bin"
        display_name = safe_filename(filename or f"{kind}_{part_number}{extension}")
        part_key = str(part_number)
        path = output_dir / f"{filename_prefix}{part_key}_{display_name}"
        data = _payload_bytes(part)
        size_bytes, sha256 = _atomic_write(path, data)
        artifacts.append(
            ExtractedArtifact(
                part_key=part_key,
                kind=kind,
                filename=display_name,
                path=path,
                mime_type=mime_type,
                disposition=disposition,
                content_id=content_id,
                size_bytes=size_bytes,
                sha256=sha256,
            )
        )
    return artifacts
