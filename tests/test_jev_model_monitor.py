"""Jev version notifications persist and deduplicate across workers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    from gmail_inbox_bot import jev_model_monitor

    monkeypatch.setattr(jev_model_monitor, "STATE_PATH", tmp_path / "state" / "jev.txt")
    messages = []
    monkeypatch.setattr(jev_model_monitor, "_notify", messages.append)
    return jev_model_monitor, messages


def test_initial_version_is_silent_and_survives_restart(monitor):
    service, messages = monitor
    service._check_version("jev-1.13.0")
    service._check_version("jev-1.13.0")

    assert service.STATE_PATH.read_text().strip() == "jev-1.13.0"
    assert messages == []


def test_changed_version_notifies_once_across_concurrent_workers(monitor):
    service, messages = monitor
    service._check_version("jev-1.13.0")
    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(service._check_version, ["jev-1.14.0"] * 30))

    assert len(messages) == 1
    assert "jev-1.13.0" in messages[0]
    assert "jev-1.14.0" in messages[0]
    assert "✅" in messages[0]
    assert service.STATE_PATH.read_text().strip() == "jev-1.14.0"
    service._check_version("jev-1.14.0")
    assert len(messages) == 1


@pytest.mark.parametrize("model", [None, "", "jev", "jev-latest", "<b>bad</b>", 42])
def test_missing_or_unversioned_models_do_not_reset_baseline(monitor, model):
    service, messages = monitor
    service._check_version("jev-1.13.0")
    service._check_version(model)

    assert service.STATE_PATH.read_text().strip() == "jev-1.13.0"
    assert messages == []


def test_state_failure_does_not_break_classification(monitor):
    service, messages = monitor
    service.STATE_PATH.parent.mkdir()
    service.STATE_PATH.mkdir()
    service._check_version("jev-1.14.0")

    assert messages == []


def test_notification_failure_does_not_repeat_or_propagate(monitor, monkeypatch):
    service, messages = monitor
    service._check_version("jev-1.13.0")

    def unavailable(message):
        messages.append(message)
        raise RuntimeError("Telegram unavailable")

    monkeypatch.setattr(service, "_notify", unavailable)
    service._check_version("jev-1.14.0")
    service._check_version("jev-1.14.0")

    assert len(messages) == 1
    assert service.STATE_PATH.read_text().strip() == "jev-1.14.0"


def test_observation_does_not_wait_for_notification(monitor, monkeypatch):
    service, _ = monitor
    entered = Event()
    release = Event()

    def slow_check(model):
        entered.set()
        release.wait(5)

    monkeypatch.setattr(service, "_check_version", slow_check)
    try:
        future = service.observe_jev_model("jev-1.14.0")
        assert entered.wait(2)
        assert not future.done()
    finally:
        release.set()
    future.result(timeout=2)
