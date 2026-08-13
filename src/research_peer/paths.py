from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _home() -> Path:
    value = os.environ.get("RESEARCH_PEER_HOME") or os.environ.get("HOME")
    if not value:
        raise RuntimeError("HOME is not set")
    return Path(value).expanduser().resolve()


@dataclass(frozen=True)
class Paths:
    home: Path
    config_dir: Path
    data_dir: Path
    state_dir: Path
    cache_dir: Path
    runtime_dir: Path
    config_file: Path
    db_file: Path
    identity_key: Path
    identity_cert: Path
    pid_file: Path
    socket_file: Path
    log_file: Path
    manifest_file: Path

    @classmethod
    def discover(cls) -> "Paths":
        home = _home()
        config_base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        data_base = Path(os.environ.get("XDG_DATA_HOME", home / ".local/share"))
        state_base = Path(os.environ.get("XDG_STATE_HOME", home / ".local/state"))
        cache_base = Path(os.environ.get("XDG_CACHE_HOME", home / ".cache"))
        runtime_value = os.environ.get("XDG_RUNTIME_DIR")
        runtime_base = Path(runtime_value) if runtime_value else state_base / "research-peer/run"
        config_dir = config_base / "research-peer"
        data_dir = data_base / "research-peer"
        state_dir = state_base / "research-peer"
        cache_dir = cache_base / "research-peer"
        runtime_dir = runtime_base / "research-peer" if runtime_value else runtime_base
        return cls(
            home=home,
            config_dir=config_dir,
            data_dir=data_dir,
            state_dir=state_dir,
            cache_dir=cache_dir,
            runtime_dir=runtime_dir,
            config_file=config_dir / "config.json",
            db_file=state_dir / "state.db",
            identity_key=config_dir / "identity.key",
            identity_cert=config_dir / "identity.crt",
            pid_file=runtime_dir / "daemon.pid",
            socket_file=runtime_dir / "daemon.sock",
            log_file=state_dir / "research-peer.log",
            manifest_file=data_dir / "install-manifest.json",
        )

    def ensure_runtime(self) -> None:
        for directory in (self.config_dir, self.data_dir, self.state_dir, self.cache_dir, self.runtime_dir):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(directory, 0o700)


def atomic_write_json(path: Path, value: Any, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, mode)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        return default


def load_config(paths: Paths) -> dict[str, Any]:
    config = load_json(paths.config_file, {}) or {}
    return {
        "user_name": config.get("user_name") or os.environ.get("USER") or "unknown",
        "listen_host": config.get("listen_host", "127.0.0.1"),
        "listen_port": int(config.get("listen_port", 0)),
        "max_message_bytes": int(config.get("max_message_bytes", 262144)),
        "clock_skew_seconds": int(config.get("clock_skew_seconds", 300)),
        "max_attempts": int(config.get("max_attempts", 12)),
    }

