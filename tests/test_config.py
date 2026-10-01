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
            assert env["GROQ_API_KEY"] == ""
            assert env["LOG_LEVEL"] == "INFO"
            assert env["ENVIRONMENT"] == "development"

    def test_load_env_exposes_jev_api_key(self, monkeypatch):
        monkeypatch.setattr("gmail_inbox_bot.config.load_dotenv", lambda: None)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
        monkeypatch.setenv("JEV_API_KEY", "apikey_test")

        assert load_env()["JEV_API_KEY"] == "apikey_test"

    def test_load_env_jev_api_key_defaults_to_empty(self, monkeypatch):
        monkeypatch.setattr("gmail_inbox_bot.config.load_dotenv", lambda: None)
        monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
        monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
        monkeypatch.delenv("JEV_API_KEY", raising=False)

        assert load_env()["JEV_API_KEY"] == ""


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

    def test_sorted_by_filename(self, tmp_path):
        (tmp_path / "b_second.yml").write_text("email: b@test.com\n")
        (tmp_path / "a_first.yml").write_text("email: a@test.com\n")
        result = load_mailbox_configs(str(tmp_path))
        assert result[0]["email"] == "a@test.com"
        assert result[1]["email"] == "b@test.com"


class TestConfigFallaAlArrancar:
    """Auditoría 2026-10-01: un YAML roto ya no deja un buzón sin procesar en silencio."""

    def test_yaml_con_error_de_sintaxis_lanza(self, tmp_path):
        import pytest

        (tmp_path / "roto.yml").write_text("email: a@b.com\nrouting: [sin cerrar\n")
        with pytest.raises(ValueError, match="roto.yml"):
            load_mailbox_configs(str(tmp_path))

    def test_yaml_que_no_es_un_mapeo_lanza(self, tmp_path):
        import pytest

        (tmp_path / "lista.yml").write_text("- a\n- b\n")
        with pytest.raises(ValueError, match="no es un mapeo"):
            load_mailbox_configs(str(tmp_path))

    def test_los_yaml_reales_del_repo_son_validos(self):
        from gmail_inbox_bot.bot import validate_mailbox_config

        configs = load_mailbox_configs("config")
        assert configs
        for config in configs:
            validate_mailbox_config(config)

    def test_accion_desconocida_en_pre_filter_o_routing_lanza(self):
        import pytest

        from gmail_inbox_bot.bot import validate_mailbox_config

        config = {
            "name": "x",
            "email": "x@y.com",
            "pre_filters": [{"name": "f", "action": "silenciar"}],
            "routing": {"spam": {"action": "borrar"}},
        }
        with pytest.raises(ValueError, match="silenciar.*borrar"):
            validate_mailbox_config(config)
