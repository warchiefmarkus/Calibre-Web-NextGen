# SPDX-License-Identifier: GPL-3.0-or-later
import io
import time
from pathlib import Path

import pytest

from cps.services.opencode_runtime import (
    OpenCodeRuntimeManager,
    _Runtime,
    is_opencode_cli_profile,
)

pytestmark = pytest.mark.unit


class _Process:
    def __init__(self, poll_value=None):
        self.poll_value = poll_value
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.poll_value

    def terminate(self):
        self.terminated = True
        self.poll_value = 0

    def wait(self, timeout=None):
        return self.poll_value

    def kill(self):
        self.killed = True
        self.poll_value = -9


def _runtime(tmp_path: Path, *, active=0, last_used=None):
    return _Runtime(
        runtime_id="profile-a",
        root=tmp_path,
        port=4096,
        process=_Process(),
        log_file=io.StringIO(),
        auth_fingerprint="hash",
        active_requests=active,
        last_used=time.monotonic() if last_used is None else last_used,
    )


def test_opencode_cli_profile_is_selected_by_reserved_endpoint():
    class Profile:
        endpoint_path = "opencode-cli"

    assert is_opencode_cli_profile(Profile()) is True
    Profile.endpoint_path = "chat/completions"
    assert is_opencode_cli_profile(Profile()) is False


def test_idle_monitor_stops_inactive_runtime(monkeypatch, tmp_path):
    manager = OpenCodeRuntimeManager()
    runtime = _runtime(tmp_path, last_used=time.monotonic() - 100)
    manager._runtimes[runtime.runtime_id] = runtime
    monkeypatch.setenv("CWNG_OPENCODE_CLI_IDLE_SECONDS", "1")

    waits = iter((False, True))
    monkeypatch.setattr(manager._stop_event, "wait", lambda seconds: next(waits))
    stopped = []
    monkeypatch.setattr(
        manager, "_stop_runtime",
        lambda item, remove_credentials: stopped.append((item.runtime_id, remove_credentials)),
    )

    manager._monitor_loop()

    assert stopped == [("profile-a", True)]
    assert manager._runtimes == {}


def test_idle_monitor_keeps_active_runtime(monkeypatch, tmp_path):
    manager = OpenCodeRuntimeManager()
    runtime = _runtime(tmp_path, active=1, last_used=time.monotonic() - 100)
    manager._runtimes[runtime.runtime_id] = runtime
    monkeypatch.setenv("CWNG_OPENCODE_CLI_IDLE_SECONDS", "1")

    waits = iter((False, True))
    monkeypatch.setattr(manager._stop_event, "wait", lambda seconds: next(waits))
    stopped = []
    monkeypatch.setattr(
        manager, "_stop_runtime",
        lambda item, remove_credentials: stopped.append(item.runtime_id),
    )

    manager._monitor_loop()

    assert stopped == []
    assert manager._runtimes[runtime.runtime_id] is runtime


def test_invalidate_marks_busy_runtime_for_idle_retirement(tmp_path):
    manager = OpenCodeRuntimeManager()
    runtime = _runtime(tmp_path, active=1)
    manager._runtimes[runtime.runtime_id] = runtime

    manager.invalidate(runtime.runtime_id)

    assert manager._runtimes[runtime.runtime_id] is runtime
    assert runtime.retire_requested is True


def test_guard_plugin_blocks_tool_execution_hook(tmp_path):
    manager = OpenCodeRuntimeManager()
    manager._write_guard_plugin(tmp_path)
    plugin = (
        tmp_path / "config" / "opencode" / "plugins" / "cwng-translation-guard.js"
    )
    source = plugin.read_text(encoding="utf-8")
    assert '"tool.execute.before"' in source
    assert "forbids tool execution" in source


def test_runtime_stop_scrubs_sensitive_state_but_keeps_caches(tmp_path):
    manager = OpenCodeRuntimeManager()
    data = tmp_path / "data" / "opencode"
    cache = tmp_path / "cache" / "opencode"
    plugin = tmp_path / "config" / "opencode" / "plugins"
    data.mkdir(parents=True)
    cache.mkdir(parents=True)
    plugin.mkdir(parents=True)
    (data / "auth.json").write_text("secret", encoding="utf-8")
    (data / "opencode.db").write_text("session text", encoding="utf-8")
    (cache / "models.json").write_text("catalog", encoding="utf-8")
    (plugin / "guard.js").write_text("guard", encoding="utf-8")
    (tmp_path / "opencode.log").write_text("runtime log", encoding="utf-8")

    runtime = _runtime(tmp_path)
    manager._stop_runtime(runtime, remove_credentials=True)

    assert not data.exists()
    assert not (tmp_path / "opencode.log").exists()
    assert (cache / "models.json").read_text(encoding="utf-8") == "catalog"
    assert (plugin / "guard.js").read_text(encoding="utf-8") == "guard"


def test_deployment_restores_cli_and_scrubs_runtime_on_service_stop():
    root = Path(__file__).resolve().parents[2]
    restore = (root / "deploy/maintenance/restore.sh").read_text(encoding="utf-8")
    unit = (root / "deploy/systemd/calibre-web-nextgen.service").read_text(encoding="utf-8")

    assert 'deploy/install/35-install-opencode-cli.sh' in restore
    assert (
        "ExecStopPost=/usr/bin/rm -rf "
        "/root/calibre/CalibreWeb/var/opencode-runtime"
    ) in unit
