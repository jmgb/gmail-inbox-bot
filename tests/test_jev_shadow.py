"""Tests for jev_shadow — clasificación sombra con Jev (TypeSafe.ai)."""

from pathlib import Path

import yaml

CRITERIA_PATH = Path("gmail_inbox_bot/prompts/clasificador_jev.yml")
CONFIG_DIR = Path("config")


class TestCriteriaFile:
    def test_every_category_has_what_not_for_examples(self):
        data = yaml.safe_load(CRITERIA_PATH.read_text(encoding="utf-8"))
        assert isinstance(data, dict) and data, "YAML vacío"
        for category, spec in data.items():
            assert set(spec) == {"what", "not_for", "examples"}, category
            assert isinstance(spec["what"], str) and spec["what"].strip(), category
            assert isinstance(spec["not_for"], str) and spec["not_for"].strip(), category
            assert isinstance(spec["examples"], list) and spec["examples"], category

    def test_categories_match_routing_of_every_mailbox(self):
        criteria = yaml.safe_load(CRITERIA_PATH.read_text(encoding="utf-8"))
        for yml in sorted(CONFIG_DIR.glob("*.yml")):
            config = yaml.safe_load(yml.read_text(encoding="utf-8"))
            assert set(criteria) == set(config["routing"]), yml.name
