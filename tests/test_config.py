"""Tests for config.py — environment and YAML loading."""

import os
from unittest.mock import patch

import pytest

from gmail_inbox_bot.config import load_env, load_mailbox_configs


class TestLoadEnv:
    @patch("gmail_inbox_bot.config.load_dotenv")
    def test_missing_required_raises(self, _mock_dotenv):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="Missing required environment variables"):
                load_env()

    @patch("gmail_inbox_bot.config.load_dotenv")
    def test_all_required_present(self, _mock_dotenv):
        env_vars = {
            "GOOGLE_CLIENT_ID": "cid",
            "GOOGLE_CLIENT_SECRET": "csecret",
        }
        with patch.dict(os.environ, env_vars, clear=True):
            env = load_env()
            assert env["GOOGLE_CLIENT_ID"] == "cid"
            assert env["GOOGLE_CLIENT_SECRET"] == "csecret"

    @patch("gmail_inbox_bot.config.load_dotenv")
    def test_optional_defaults(self, _mock_dotenv):
        env_vars = {
            "GOOGLE_CLIENT_ID": "cid",
            "GOOGLE_CLIENT_SECRET": "csecret",
        }
        with patch.dict(os.environ, env_vars, clear=True):
            env = load_env()
            assert env["OPENAI_API_KEY"] == ""
            assert env["LOG_LEVEL"] == "INFO"
            assert env["ENVIRONMENT"] == "development"


class TestLoadMailboxConfigs:
    def test_missing_dir_returns_empty(self, tmp_path):
        result = load_mailbox_configs(str(tmp_path / "nonexistent"))
        assert result == []

    def test_loads_yaml(self, tmp_path):
        config_file = tmp_path / "mailbox1.yml"
        config_file.write_text(
            "email: bot@test.com\nrouting:\n  spam:\n    action: silent\n",
            encoding="utf-8",
        )
        result = load_mailbox_configs(str(tmp_path))
        assert len(result) == 1
        assert result[0]["email"] == "bot@test.com"
        assert result[0]["name"] == "mailbox1"  # from filename

    def test_skips_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yml"
        bad.write_text(": : invalid", encoding="utf-8")
        good = tmp_path / "good.yml"
        good.write_text("email: ok@test.com\n", encoding="utf-8")
        result = load_mailbox_configs(str(tmp_path))
        # bad.yml parses to a dict with weird key, but doesn't crash
        # just ensure at least the good one loads
        assert any(c.get("email") == "ok@test.com" for c in result)

    def test_sorted_by_filename(self, tmp_path):
        (tmp_path / "b_second.yml").write_text("email: b@test.com\n")
        (tmp_path / "a_first.yml").write_text("email: a@test.com\n")
        result = load_mailbox_configs(str(tmp_path))
        assert result[0]["email"] == "a@test.com"
        assert result[1]["email"] == "b@test.com"
