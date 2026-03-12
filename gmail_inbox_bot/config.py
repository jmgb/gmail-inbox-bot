"""Configuration loader — reads YAML mailbox configs and .env settings."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from .logger import setup_logger

log = setup_logger("gmail_inbox_bot.config", "logs/app.log")


def load_env() -> dict[str, str]:
    """Load .env and return shared environment variables.

    Per-account credentials (refresh token, email, send_as, query) live in
    each mailbox YAML config, not here.  The .env only holds secrets shared
    across all mailboxes (OAuth client, OpenAI key, etc.).
    """
    load_dotenv()
    required = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
    env: dict[str, str] = {}
    missing: list[str] = []
    for key in required:
        val = os.environ.get(key, "")
        if not val:
            missing.append(key)
        env[key] = val

    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    env["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")
    env["LOG_LEVEL"] = os.environ.get("LOG_LEVEL", "INFO")
    env["ENVIRONMENT"] = os.environ.get("ENVIRONMENT", "development")
    return env


def load_mailbox_configs(config_dir: str = "config") -> list[dict]:
    """Load all YAML files from *config_dir* as mailbox configurations.

    Each YAML file represents one mailbox the bot monitors.
    Returns the list sorted by filename.
    """
    config_path = Path(config_dir)
    if not config_path.is_dir():
        log.warning("Config directory '%s' not found — using empty config list", config_dir)
        return []

    configs: list[dict] = []
    for yml_file in sorted(config_path.glob("*.y*ml")):
        try:
            data = yaml.safe_load(yml_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Ensure the config has a name for logging
                data.setdefault("name", yml_file.stem)
                configs.append(data)
                log.info("Loaded mailbox config: %s", data.get("name"))
        except Exception:
            log.exception("Failed to load config file: %s", yml_file)

    return configs
