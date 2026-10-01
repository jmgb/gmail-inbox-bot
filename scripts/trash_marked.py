"""Safely review and (after explicit confirmation) move marked Gmail messages to trash."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from gmail_inbox_bot.bot import _build_gmail_client
from gmail_inbox_bot.config import load_env, load_mailbox_configs


class ValidationError(RuntimeError):
    """The local archive is incomplete or inconsistent, so deletion is blocked."""


def _archive_path(root: Path, relative: str, label: str) -> Path:
    if not relative:
        raise ValidationError(f"{label} no puede estar vacío")
    root_resolved = root.resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValidationError(f"{label} está fuera del archivo local: {relative}") from exc
    return candidate


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(
    root: Path, relative: str, expected_size: int, expected_sha: str, label: str
) -> Path:
    path = _archive_path(root, relative, label)
    if not path.is_file():
        raise ValidationError(f"{label} no existe: {relative}")
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise ValidationError(
            f"tamaño inconsistente en {label}: {relative} ({actual_size} != {expected_size})"
        )
    actual_sha = _sha256(path)
    if actual_sha != expected_sha:
        raise ValidationError(f"SHA-256 inconsistente en {label}: {relative}")
    return path


def _open_manifest(root: Path) -> sqlite3.Connection:
    database = root / ".state.sqlite3"
    if not database.is_file():
        raise ValidationError(f"no existe el manifiesto local: {database}")
    connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def validate_marked_messages(messages_csv: Path) -> list[dict]:
    """Validate every marked row and return safe, manifest-backed records.

    The database is authoritative for file paths, sizes, hashes and Gmail
    labels; the CSV supplies the user's ``borrar`` selection and optional
    ``conservar`` protection marker.
    """
    if not messages_csv.is_file():
        raise ValidationError(f"no existe el CSV: {messages_csv}")
    root = messages_csv.parent
    with messages_csv.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"cuenta", "message_id", "borrar", "ruta_eml", "sha256_eml"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValidationError(f"faltan columnas en el CSV: {', '.join(sorted(missing))}")
        rows = list(reader)

    keys: set[tuple[str, str]] = set()
    marked_rows: list[dict] = []
    for row in rows:
        key = (row.get("cuenta", "").strip(), row.get("message_id", "").strip())
        if key in keys:
            raise ValidationError(f"mensaje duplicado en CSV: {key}")
        keys.add(key)
        marker = row.get("borrar", "").strip().lower()
        if marker not in {"", "x"}:
            raise ValidationError(f"marca borrar inválida para {key}: {marker!r}")
        keep_marker = row.get("conservar", "").strip().lower()
        if keep_marker not in {"", "x"}:
            raise ValidationError(f"marca conservar inválida para {key}: {keep_marker!r}")
        if marker == "x" and keep_marker == "x":
            raise ValidationError(f"un mensaje no puede tener conservar y borrar: {key}")
        if marker == "x":
            marked_rows.append(row)

    if not marked_rows:
        return []

    connection = _open_manifest(root)
    try:
        validated: list[dict] = []
        for row in marked_rows:
            account = row["cuenta"].strip()
            message_id = row["message_id"].strip()
            message = connection.execute(
                "SELECT * FROM messages WHERE account = ? AND message_id = ?",
                (account, message_id),
            ).fetchone()
            if message is None:
                raise ValidationError(f"mensaje ausente del manifiesto: {account}/{message_id}")
            if message["status"] != "completed" or message["last_error"]:
                raise ValidationError(f"mensaje no completado: {account}/{message_id}")
            if row.get("estado_archivo", "").strip() != message["status"]:
                raise ValidationError(
                    f"estado CSV/manifiesto inconsistente: {account}/{message_id}"
                )
            if row.get("ruta_eml", "").strip() != message["eml_path"]:
                raise ValidationError(
                    f"ruta EML CSV/manifiesto inconsistente: {account}/{message_id}"
                )
            if row.get("sha256_eml", "").strip() != message["eml_sha256"]:
                raise ValidationError(
                    f"hash EML CSV/manifiesto inconsistente: {account}/{message_id}"
                )

            _verify_file(
                root,
                message["eml_path"],
                int(message["eml_size"]),
                message["eml_sha256"],
                "EML",
            )
            artifacts = connection.execute(
                "SELECT * FROM artifacts WHERE account = ? AND message_id = ? ORDER BY part_key",
                (account, message_id),
            ).fetchall()
            for artifact in artifacts:
                if artifact["status"] != "completed" or artifact["last_error"]:
                    raise ValidationError(f"adjunto no completado: {account}/{message_id}")
                _verify_file(
                    root,
                    artifact["local_path"],
                    int(artifact["size_bytes"]),
                    artifact["sha256"],
                    f"adjunto {artifact['part_key']}",
                )

            try:
                expected_count = int(row.get("numero_ficheros", ""))
            except ValueError as exc:
                raise ValidationError(f"numero_ficheros inválido: {account}/{message_id}") from exc
            if expected_count != len(artifacts):
                raise ValidationError(f"número de adjuntos inconsistente: {account}/{message_id}")
            labels = json.loads(message["label_ids"] or "[]")
            validated.append(
                {
                    **row,
                    "cuenta": account,
                    "message_id": message_id,
                    "thread_id": message["thread_id"],
                    "labels": labels,
                    "artifacts_count": len(artifacts),
                    "artifacts_bytes": sum(int(item["size_bytes"]) for item in artifacts),
                }
            )
        return validated
    finally:
        connection.close()


def confirm_trash(count: int, input_fn: Callable[[str], str] = input) -> bool:
    """Ask for the non-ambiguous confirmation phrase required for execution."""
    answer = input_fn(f"Escribe exactamente TRASH {count} para continuar: ")
    return answer.strip() == f"TRASH {count}"


def _select_mailbox(configs: list[dict], account: str) -> dict:
    for config in configs:
        if account in {config.get("email"), config.get("name")}:
            return config
    raise ValidationError(f"cuenta no encontrada en config/: {account}")


def execute_trash(
    messages: list[dict],
    env: dict[str, str],
    configs: list[dict],
    on_result: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Move validated messages to Gmail trash, preserving per-message results.

    *on_result* recibe cada resultado en cuanto existe, para que la auditoría quede escrita
    aunque la pasada se corte a mitad (Ctrl-C, error no capturado).
    """
    clients: dict[str, object] = {}
    results: list[dict] = []

    def record(result: dict) -> None:
        results.append(result)
        if on_result is not None:
            on_result(result)

    try:
        for message in messages:
            account = message["cuenta"]
            if "TRASH" in message.get("labels", []):
                record({**message, "resultado": "already_in_trash", "error": ""})
                continue
            try:
                if account not in clients:
                    clients[account] = _build_gmail_client(
                        env,
                        _select_mailbox(configs, account),
                        request_rate_per_second=3.0,
                        request_retries=5,
                    )
                clients[account].delete_email(account, message["message_id"])
                record({**message, "resultado": "trashed", "error": ""})
            except Exception as exc:  # noqa: BLE001 - continue and audit every message
                record({**message, "resultado": "error", "error": f"{type(exc).__name__}: {exc}"})
        return results
    finally:
        for client in clients.values():
            http = getattr(client, "_http", None)
            if http is not None:
                http.close()


RESULT_FIELDS = ["fecha", "cuenta", "message_id", "resultado", "error"]


def open_results(path: Path):
    """Abre la auditoría en modo append (cabecera solo si es nueva) y devuelve el escritor.

    Antes se escribía todo al final con "w": un corte a mitad dejaba mensajes en la papelera
    sin registro, y cada ejecución machacaba la auditoría de la anterior.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists() or path.stat().st_size == 0
    handle = path.open("a", encoding="utf-8", newline="")
    writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, lineterminator="\n")
    if is_new:
        writer.writeheader()
        handle.flush()

    def write(result: dict) -> None:
        writer.writerow(
            {
                "fecha": datetime.now(timezone.utc).isoformat(),
                "cuenta": result["cuenta"],
                "message_id": result["message_id"],
                "resultado": result["resultado"],
                "error": result.get("error", ""),
            }
        )
        handle.flush()

    return handle, write


def main() -> int:
    parser = argparse.ArgumentParser(description="Review and safely trash marked Gmail messages")
    parser.add_argument(
        "--messages", type=Path, required=True, help="messages.csv from the archive"
    )
    parser.add_argument(
        "--execute", action="store_true", help="move messages after TTY confirmation"
    )
    parser.add_argument(
        "--results", type=Path, help="audit CSV (default: trash_results.csv beside messages.csv)"
    )
    args = parser.parse_args()
    try:
        messages = validate_marked_messages(args.messages)
        total_bytes = sum(item["artifacts_bytes"] for item in messages)
        total_artifacts = sum(item["artifacts_count"] for item in messages)
        print(f"mensajes marcados={len(messages)} adjuntos={total_artifacts} bytes={total_bytes}")
        if not args.execute or not messages:
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise ValidationError("--execute requiere una terminal interactiva (TTY)")
        if not confirm_trash(len(messages)):
            print("cancelado: no se ha escrito la frase exacta")
            return 2
        results_path = args.results or args.messages.with_name("trash_results.csv")
        handle, write = open_results(results_path)
        try:
            results = execute_trash(messages, load_env(), load_mailbox_configs(), on_result=write)
        finally:
            handle.close()
        failed = sum(result["resultado"] == "error" for result in results)
        print(f"procesados={len(results)} errores={failed} auditoría={results_path}")
        return 1 if failed else 0
    except (ValidationError, RuntimeError, ValueError) as exc:
        print(f"estado inválido: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
