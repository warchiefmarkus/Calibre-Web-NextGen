# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy OpenCode CLI runtime for reader translation profiles.

Each OpenCode-CLI profile gets an isolated headless server and credential store.
Servers start on first use, restart after crashes/key changes, and are stopped
after an idle timeout so OpenCode does not stay resident indefinitely.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import requests

from .. import logger

log = logger.create()

OPENCODE_CLI_ENDPOINT = "opencode-cli"
_DEFAULT_IDLE_SECONDS = 600
_DEFAULT_MONITOR_SECONDS = 30
_DEFAULT_STARTUP_SECONDS = 15
_PROFILE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")


class OpenCodeRuntimeError(RuntimeError):
    pass


@dataclass
class _Runtime:
    runtime_id: str
    root: Path
    port: int
    process: subprocess.Popen
    log_file: Any
    auth_fingerprint: str
    active_requests: int = 0
    last_used: float = 0.0
    retire_requested: bool = False

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class OpenCodeRuntimeManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runtimes: dict[str, _Runtime] = {}
        self._monitor: threading.Thread | None = None
        self._stop_event = threading.Event()

    @staticmethod
    def _env_seconds(name: str, default: int, minimum: int = 1) -> int:
        try:
            return max(minimum, int(os.environ.get(name, default)))
        except (TypeError, ValueError):
            return default

    @property
    def idle_seconds(self) -> int:
        return self._env_seconds("CWNG_OPENCODE_CLI_IDLE_SECONDS", _DEFAULT_IDLE_SECONDS)

    @property
    def monitor_seconds(self) -> int:
        return self._env_seconds("CWNG_OPENCODE_CLI_MONITOR_SECONDS", _DEFAULT_MONITOR_SECONDS)

    @property
    def startup_seconds(self) -> int:
        return self._env_seconds("CWNG_OPENCODE_CLI_STARTUP_SECONDS", _DEFAULT_STARTUP_SECONDS)

    @property
    def runtime_root(self) -> Path:
        default = "/root/calibre/CalibreWeb/var/opencode-runtime"
        return Path(os.environ.get("CWNG_OPENCODE_CLI_RUNTIME_DIR", default))

    @property
    def binary(self) -> str:
        configured = os.environ.get("CWNG_OPENCODE_CLI_BIN", "").strip()
        binary = configured or shutil.which("opencode") or ""
        if not binary:
            raise OpenCodeRuntimeError(
                "OpenCode CLI is not installed. Install the 'opencode' command or set "
                "CWNG_OPENCODE_CLI_BIN."
            )
        return binary

    @staticmethod
    def _fingerprint(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

    @staticmethod
    def _free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _profile_root(self, runtime_id: str) -> Path:
        base = self.runtime_root
        base.mkdir(parents=True, exist_ok=True)
        os.chmod(base, 0o700)
        safe = _PROFILE_ID_RE.sub("_", runtime_id)[:96] or "default"
        return base / safe

    @staticmethod
    def _health(base_url: str, timeout: float = 1.0) -> bool:
        try:
            response = requests.get(f"{base_url}/global/health", timeout=timeout)
            payload = response.json() if response.ok else {}
            return bool(response.ok and isinstance(payload, dict) and payload.get("healthy"))
        except (requests.RequestException, ValueError):
            return False

    def _write_auth(self, root: Path, api_key: str) -> None:
        auth_dir = root / "data" / "opencode"
        auth_dir.mkdir(parents=True, exist_ok=True)
        auth_path = auth_dir / "auth.json"
        temp_path = auth_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps({"opencode": {"type": "api", "key": api_key}}),
            encoding="utf-8",
        )
        os.chmod(temp_path, 0o600)
        temp_path.replace(auth_path)
        os.chmod(auth_path, 0o600)

    def _write_guard_plugin(self, root: Path) -> None:
        plugin_dir = root / "config" / "opencode" / "plugins"
        plugin_dir.mkdir(parents=True, exist_ok=True)
        plugin = plugin_dir / "cwng-translation-guard.js"
        plugin.write_text(
            "export const CwngTranslationGuard = async () => ({\n"
            "  \"tool.execute.before\": async (input) => {\n"
            "    throw new Error(\"Calibre Web translation runtime forbids tool execution: \" + input.tool)\n"
            "  },\n"
            "})\n",
            encoding="utf-8",
        )
        os.chmod(plugin, 0o600)

    def _environment(self, root: Path) -> dict[str, str]:
        env = os.environ.copy()
        env.update({
            "HOME": str(root),
            "XDG_DATA_HOME": str(root / "data"),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        })
        return env

    def _tail_log(self, root: Path, limit: int = 1200) -> str:
        try:
            text = (root / "opencode.log").read_text(encoding="utf-8", errors="replace")
            return text[-limit:].strip()
        except OSError:
            return ""

    def _start(self, runtime_id: str, api_key: str) -> _Runtime:
        if not api_key:
            raise OpenCodeRuntimeError("OpenCode Zen API key is required for OpenCode CLI.")
        root = self._profile_root(runtime_id)
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        (root / "config").mkdir(exist_ok=True)
        (root / "cache").mkdir(exist_ok=True)
        os.chmod(root / "config", 0o700)
        os.chmod(root / "cache", 0o700)
        self._write_auth(root, api_key)
        self._write_guard_plugin(root)
        port = self._free_port()
        log_handle = open(root / "opencode.log", "a", encoding="utf-8")
        process = subprocess.Popen(
            [
                self.binary, "serve",
                "--hostname", "127.0.0.1", "--port", str(port),
                "--log-level", "ERROR",
            ],
            cwd=root,
            env=self._environment(root),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            close_fds=True,
        )
        runtime = _Runtime(
            runtime_id=runtime_id,
            root=root,
            port=port,
            process=process,
            log_file=log_handle,
            auth_fingerprint=self._fingerprint(api_key),
            last_used=time.monotonic(),
        )
        deadline = time.monotonic() + self.startup_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = self._tail_log(root)
                self._stop_runtime(runtime, remove_credentials=True)
                raise OpenCodeRuntimeError(
                    "OpenCode CLI exited during startup"
                    + (f": {detail}" if detail else ".")
                )
            if self._health(runtime.base_url):
                log.info("OpenCode CLI runtime %s started on 127.0.0.1:%d", runtime_id, port)
                return runtime
            time.sleep(0.1)
        self._stop_runtime(runtime, remove_credentials=True)
        raise OpenCodeRuntimeError(
            f"OpenCode CLI did not become healthy within {self.startup_seconds} seconds."
        )

    def _stop_runtime(self, runtime: _Runtime, *, remove_credentials: bool) -> None:
        process = runtime.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        try:
            runtime.log_file.close()
        except Exception:
            pass
        if remove_credentials:
            # Scrub credentials and persisted session/message state after every
            # idle/crash stop, but keep dependency/model caches. Rebuilding the
            # OpenCode config tree is expensive (~cold-start seconds and
            # hundreds of MiB of package cache) and does not improve secrecy.
            for path in (
                runtime.root / "data" / "opencode",
                runtime.root / "opencode.log",
            ):
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    log.warning("Could not scrub OpenCode CLI runtime state %s", path)

    def _ensure_monitor(self) -> None:
        if self._monitor and self._monitor.is_alive():
            return
        self._stop_event.clear()
        self._monitor = threading.Thread(
            target=self._monitor_loop,
            name="cwng-opencode-runtime-monitor",
            daemon=True,
        )
        self._monitor.start()

    def _monitor_loop(self) -> None:
        while not self._stop_event.wait(self.monitor_seconds):
            now = time.monotonic()
            with self._lock:
                for runtime_id, runtime in list(self._runtimes.items()):
                    dead = runtime.process.poll() is not None
                    idle = (
                        runtime.active_requests == 0
                        and now - runtime.last_used >= self.idle_seconds
                    )
                    if dead or idle:
                        reason = "exited" if dead else "idle timeout"
                        log.info("Stopping OpenCode CLI runtime %s (%s)", runtime_id, reason)
                        self._stop_runtime(runtime, remove_credentials=True)
                        self._runtimes.pop(runtime_id, None)

    def _get_or_start(self, runtime_id: str, api_key: str) -> _Runtime:
        fingerprint = self._fingerprint(api_key)
        runtime = self._runtimes.get(runtime_id)
        if runtime is not None:
            healthy = runtime.process.poll() is None and self._health(runtime.base_url)
            key_matches = runtime.auth_fingerprint == fingerprint
            if healthy and key_matches:
                return runtime
            if runtime.active_requests:
                raise OpenCodeRuntimeError(
                    "OpenCode CLI credentials changed while the runtime is busy."
                )
            self._stop_runtime(runtime, remove_credentials=True)
            self._runtimes.pop(runtime_id, None)
        runtime = self._start(runtime_id, api_key)
        self._runtimes[runtime_id] = runtime
        self._ensure_monitor()
        return runtime

    @contextlib.contextmanager
    def lease(self, runtime_id: str, api_key: str) -> Iterator[str]:
        with self._lock:
            runtime = self._get_or_start(runtime_id, api_key)
            runtime.active_requests += 1
            runtime.last_used = time.monotonic()
        try:
            yield runtime.base_url
        finally:
            with self._lock:
                current = self._runtimes.get(runtime_id)
                if current is runtime:
                    current.active_requests = max(0, current.active_requests - 1)
                    current.last_used = (
                        0.0 if current.retire_requested else time.monotonic()
                    )

    def invalidate(self, runtime_id: str) -> None:
        with self._lock:
            runtime = self._runtimes.get(runtime_id)
            if runtime is None:
                return
            if runtime.active_requests:
                # Do not orphan or kill an in-flight request. Mark it for
                # retirement; lease() makes it immediately idle on release.
                runtime.retire_requested = True
                return
            self._runtimes.pop(runtime_id, None)
            self._stop_runtime(runtime, remove_credentials=True)

    def shutdown(self) -> None:
        self._stop_event.set()
        with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
            for runtime in runtimes:
                self._stop_runtime(runtime, remove_credentials=True)


_MANAGER = OpenCodeRuntimeManager()


def runtime_manager() -> OpenCodeRuntimeManager:
    return _MANAGER


def is_opencode_cli_profile(profile: Any) -> bool:
    return str(getattr(profile, "endpoint_path", "") or "").strip().lower() == OPENCODE_CLI_ENDPOINT
