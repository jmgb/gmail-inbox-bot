"""Tests for telegram_activity.py — registro best-effort para el triage matinal."""

import json
from unittest.mock import patch

from gmail_inbox_bot import telegram_activity

CAMPOS = {"ts", "repo", "host", "nivel", "referencia", "texto"}


class TestRegistrar:
    def test_writes_json_line_with_six_fields(self, tmp_path):
        destino = tmp_path / "gmail-inbox-bot.jsonl"
        env = {"TELEGRAM_ACTIVITY_LOG": str(destino), "TELEGRAM_ACTIVITY_HOST": "finanzas"}
        with patch.dict("os.environ", env):
            telegram_activity.registrar("⚠️ Fallo en recordatorios", referencia="reminder_failure")
            telegram_activity.registrar("🚨 Excepción", referencia="telegram_logger")
            telegram_activity.registrar("Todo bien")

        lines = destino.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 3
        rows = [json.loads(line) for line in lines]
        for row in rows:
            assert set(row) == CAMPOS
            assert row["repo"] == "gmail-inbox-bot"
            assert row["host"] == "finanzas"
            assert row["ts"].endswith("Z")
        assert [r["nivel"] for r in rows] == ["warning", "error", "ok"]
        assert rows[0]["referencia"] == "reminder_failure"
        assert rows[0]["texto"] == "⚠️ Fallo en recordatorios"
        assert rows[2]["referencia"] == ""

    def test_explicit_level_wins(self, tmp_path):
        destino = tmp_path / "x.jsonl"
        with patch.dict("os.environ", {"TELEGRAM_ACTIVITY_LOG": str(destino)}):
            telegram_activity.registrar("sin emoji", nivel="error")
        assert json.loads(destino.read_text(encoding="utf-8"))["nivel"] == "error"

    def test_truncates_to_1000_bytes_without_breaking_utf8(self, tmp_path):
        destino = tmp_path / "x.jsonl"
        texto = "ñ" * 700  # 1400 bytes; 1000 bytes partiría la ñ número 501
        with patch.dict("os.environ", {"TELEGRAM_ACTIVITY_LOG": str(destino)}):
            telegram_activity.registrar(texto)
        recorte = json.loads(destino.read_text(encoding="utf-8"))["texto"]
        assert len(recorte.encode("utf-8")) <= 1000
        assert recorte == "ñ" * 500

    def test_unwritable_destination_does_not_raise(self, tmp_path):
        destino = tmp_path / "no" / "existe" / "x.jsonl"
        with patch.dict("os.environ", {"TELEGRAM_ACTIVITY_LOG": str(destino)}):
            telegram_activity.registrar("⚠️ algo")  # no debe lanzar
        assert not destino.exists()

    def test_no_destination_is_noop(self, tmp_path):
        with (
            patch.dict("os.environ", {"TELEGRAM_ACTIVITY_LOG": ""}),
            patch.object(telegram_activity.Path, "is_dir", return_value=False),
        ):
            assert telegram_activity._destino() is None
            telegram_activity.registrar("⚠️ algo")  # no-op silencioso

    def test_fallback_writer_has_host_suffix(self):
        assert telegram_activity._WRITER == "gmail-inbox-bot-host"
        assert telegram_activity._REPO == "gmail-inbox-bot"
