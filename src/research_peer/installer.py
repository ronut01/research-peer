from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

from . import __version__
from .identity import Identity
from .paths import Paths, atomic_write_json, load_config, load_json
from .store import Store


class InstallSafetyError(RuntimeError):
    pass


def _actual_account_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()


def _systemd_allowed(paths: Paths) -> bool:
    return paths.home == _actual_account_home() and os.environ.get("RESEARCH_PEER_TESTING") != "1"


def _sha256(path: Path) -> str | None:
    if not path.is_file() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(path: Path, category: str, kind: str | None = None) -> dict[str, Any]:
    if kind is None:
        if path.is_symlink():
            kind = "symlink"
        elif path.is_dir():
            kind = "directory"
        else:
            kind = "file"
    return {"path": str(path), "category": category, "kind": kind, "sha256": _sha256(path)}


def _copy_file(source: Path, target: Path, mode: int = 0o644) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, target, follow_symlinks=False)
    os.chmod(target, mode)


def _copy_tree(source: Path, target: Path, records: list[dict[str, Any]], category: str) -> None:
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    records.append(_record(target, category, "directory"))
    for source_path in sorted(source.rglob("*")):
        relative = source_path.relative_to(source)
        if "__pycache__" in relative.parts or source_path.suffix in {".pyc", ".pyo"}:
            continue
        target_path = target / relative
        if source_path.is_symlink():
            target_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target_path.symlink_to(os.readlink(source_path))
            records.append(_record(target_path, category, "symlink"))
        elif source_path.is_dir():
            target_path.mkdir(parents=True, exist_ok=True, mode=0o700)
            records.append(_record(target_path, category, "directory"))
        elif source_path.is_file():
            _copy_file(source_path, target_path, stat.S_IMODE(source_path.stat().st_mode) or 0o644)
            records.append(_record(target_path, category, "file"))


def _assert_owned_or_absent(path: Path, previous: dict[str, Any] | None) -> None:
    if not path.exists() and not path.is_symlink():
        return
    previous_paths = {item["path"] for item in (previous or {}).get("items", [])}
    if str(path) not in previous_paths:
        raise InstallSafetyError(f"refusing to overwrite unowned path: {path}")


def _assert_shortcut_command_available(path: Path, previous: dict[str, Any] | None) -> None:
    resolved = shutil.which(path.name)
    if not resolved:
        return
    previous_paths = {item["path"] for item in (previous or {}).get("items", [])}
    resolved_path = Path(resolved).absolute()
    if resolved_path != path.absolute() or str(path) not in previous_paths:
        raise InstallSafetyError(f"refusing to shadow existing command: {path.name} resolves to {resolved_path}")


def install(source_root: Path, paths: Paths | None = None) -> dict[str, Any]:
    paths = paths or Paths.discover()
    source_root = source_root.resolve()
    required = [source_root / "src/research_peer", source_root / "channel/research-peer-channel.mjs", source_root / "channel/security.mjs", source_root / "plugin/.claude-plugin/plugin.json", source_root / "plugin/.mcp.json", source_root / "plugin/skills", source_root / "skill/SKILL.md"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("source tree is incomplete: " + ", ".join(missing))
    previous = load_json(paths.manifest_file, None)
    app_dir = paths.data_dir / "app"
    bin_dir = paths.home / ".local/bin"
    cli_path = bin_dir / "research-peer"
    shortcut_path = bin_dir / "rp"
    personal_skill = paths.home / ".claude/skills/research-peer"
    plugin_dir = paths.home / ".claude/skills/research-peer-plugin"
    service_file = paths.home / ".config/systemd/user/research-peer.service"
    for target in (app_dir, cli_path, shortcut_path, personal_skill, plugin_dir, service_file):
        _assert_owned_or_absent(target, previous)
    _assert_shortcut_command_available(shortcut_path, previous)
    paths.ensure_runtime()
    if previous:
        _remove_program_items(previous, preserve_manifest=True)

    records: list[dict[str, Any]] = []
    _copy_tree(source_root / "src/research_peer", app_dir / "research_peer", records, "program")
    records.append(_record(app_dir, "program", "directory"))
    _copy_file(source_root / "channel/research-peer-channel.mjs", app_dir / "channel/research-peer-channel.mjs", 0o755)
    _copy_file(source_root / "channel/security.mjs", app_dir / "channel/security.mjs", 0o644)
    records.extend([
        _record(app_dir / "channel", "program", "directory"),
        _record(app_dir / "channel/research-peer-channel.mjs", "program"),
        _record(app_dir / "channel/security.mjs", "program"),
    ])
    for name in ("package.json", "package-lock.json"):
        if (source_root / name).exists():
            _copy_file(source_root / name, app_dir / name)
            records.append(_record(app_dir / name, "program"))
    if (source_root / "node_modules").exists():
        _copy_tree(source_root / "node_modules", app_dir / "node_modules", records, "program")

    bin_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    wrapper = f"#!/bin/sh\nPYTHONPATH='{app_dir}' exec python3 -m research_peer \"$@\"\n"
    cli_path.write_text(wrapper, encoding="utf-8")
    os.chmod(cli_path, 0o755)
    records.append(_record(cli_path, "program"))
    shortcut_wrapper = f"#!/bin/sh\nPYTHONPATH='{app_dir}' exec python3 -m research_peer.rp \"$@\"\n"
    shortcut_path.write_text(shortcut_wrapper, encoding="utf-8")
    os.chmod(shortcut_path, 0o755)
    records.append(_record(shortcut_path, "program"))

    personal_skill.mkdir(parents=True, exist_ok=True, mode=0o700)
    _copy_file(source_root / "skill/SKILL.md", personal_skill / "SKILL.md")
    records.extend([_record(personal_skill, "program", "directory"), _record(personal_skill / "SKILL.md", "program")])

    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True, mode=0o700)
    (plugin_dir / "channel").mkdir(parents=True, exist_ok=True, mode=0o700)
    _copy_file(source_root / "plugin/.claude-plugin/plugin.json", plugin_dir / ".claude-plugin/plugin.json")
    _copy_file(source_root / "plugin/.mcp.json", plugin_dir / ".mcp.json")
    _copy_tree(source_root / "plugin/skills", plugin_dir / "skills", records, "program")
    _copy_file(source_root / "channel/research-peer-channel.mjs", plugin_dir / "channel/research-peer-channel.mjs", 0o755)
    _copy_file(source_root / "channel/security.mjs", plugin_dir / "channel/security.mjs", 0o644)
    node_link = plugin_dir / "node_modules"
    if (app_dir / "node_modules").exists():
        node_link.symlink_to(app_dir / "node_modules", target_is_directory=True)
    records.extend([
        _record(plugin_dir, "program", "directory"), _record(plugin_dir / ".claude-plugin", "program", "directory"),
        _record(plugin_dir / ".claude-plugin/plugin.json", "program"), _record(plugin_dir / "channel", "program", "directory"),
        _record(plugin_dir / ".mcp.json", "program"),
        _record(plugin_dir / "channel/research-peer-channel.mjs", "program"),
        _record(plugin_dir / "channel/security.mjs", "program"),
    ])
    if node_link.is_symlink():
        records.append(_record(node_link, "program", "symlink"))

    service_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    service = f"""[Unit]
Description=Research Peer P2P daemon
After=network-online.target

[Service]
Type=simple
ExecStart={cli_path} daemon
Restart=on-failure
RestartSec=3
UMask=0077

[Install]
WantedBy=default.target
"""
    service_file.write_text(service, encoding="utf-8")
    os.chmod(service_file, 0o644)
    records.append(_record(service_file, "program"))

    config = load_config(paths)
    if not paths.config_file.exists():
        atomic_write_json(paths.config_file, config)
    identity = Identity.load_or_create(paths, config["user_name"])
    store = Store(paths.db_file)
    store.close()
    for item in (paths.config_dir, paths.config_file, paths.identity_key, paths.identity_cert, paths.state_dir, paths.db_file, paths.cache_dir, paths.runtime_dir, paths.data_dir):
        records.append(_record(item, "data" if item not in {paths.cache_dir, paths.runtime_dir} else item.name, "directory" if item.is_dir() else None))

    manifest = {
        "manifest_version": 1, "product": "research-peer", "version": __version__,
        "home": str(paths.home), "items": _deduplicate(records),
        "exclusive_directories": [
            str(app_dir), str(personal_skill), str(plugin_dir), str(paths.config_dir),
            str(paths.state_dir), str(paths.cache_dir), str(paths.runtime_dir),
        ],
    }
    atomic_write_json(paths.manifest_file, manifest)
    manifest["items"].append(_record(paths.manifest_file, "program"))
    atomic_write_json(paths.manifest_file, manifest)
    if _systemd_allowed(paths):
        subprocess.run(["systemctl", "--user", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl", "--user", "enable", "research-peer.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {
        "installed": True, "version": __version__, "cli": str(cli_path),
        "shortcut": str(shortcut_path),
        "plugin": str(plugin_dir), "skill": str(personal_skill),
        "service": str(service_file), "fingerprint": identity.fingerprint,
        "manifest": str(paths.manifest_file),
    }


def _deduplicate(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in records:
        result[record["path"]] = record
    return sorted(result.values(), key=lambda item: (item["kind"] == "directory", item["path"]))


def _remove_program_items(manifest: dict[str, Any], preserve_manifest: bool = False) -> None:
    items = [item for item in manifest.get("items", []) if item.get("category") == "program"]
    if preserve_manifest:
        items = [item for item in items if not item["path"].endswith("install-manifest.json")]
    _remove_manifest_items(items)


def _remove_manifest_items(items: list[dict[str, Any]]) -> None:
    files = [item for item in items if item.get("kind") != "directory"]
    directories = [item for item in items if item.get("kind") == "directory"]
    for item in sorted(files, key=lambda value: len(value["path"]), reverse=True):
        path = Path(item["path"])
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
        except FileNotFoundError:
            pass
    for item in sorted(directories, key=lambda value: len(value["path"]), reverse=True):
        try:
            Path(item["path"]).rmdir()
        except (FileNotFoundError, OSError):
            pass


def _safe_remove_exclusive(
    directory: Path,
    allowed: set[Path],
    home: Path,
    runtime_dir: Path | None = None,
) -> None:
    resolved_parent = directory.parent.resolve()
    candidate = resolved_parent / directory.name
    home_owned = home in candidate.parents
    exact_runtime = (
        runtime_dir is not None
        and directory == runtime_dir
        and candidate == runtime_dir.parent.resolve() / "research-peer"
        and runtime_dir.name == "research-peer"
    )
    if (
        directory not in allowed
        or candidate == home
        or candidate == Path("/")
        or not (home_owned or exact_runtime)
    ):
        raise InstallSafetyError(f"refusing unsafe directory removal: {directory}")
    if directory.is_symlink():
        directory.unlink()
        return
    if not directory.exists():
        return
    for entry in os.scandir(directory):
        path = Path(entry.path)
        if entry.is_symlink():
            path.unlink()
        elif entry.is_dir(follow_symlinks=False):
            _safe_remove_child(path, directory)
        else:
            path.unlink()
    directory.rmdir()


def _safe_remove_child(path: Path, root: Path) -> None:
    if path.is_symlink():
        path.unlink()
        return
    for entry in os.scandir(path):
        child = Path(entry.path)
        if root.resolve() not in child.resolve().parents:
            raise InstallSafetyError(f"child escaped owned directory: {child}")
        if entry.is_symlink():
            child.unlink()
        elif entry.is_dir(follow_symlinks=False):
            _safe_remove_child(child, root)
        else:
            child.unlink()
    path.rmdir()


def uninstall(paths: Paths, *, dry_run: bool, keep_data: bool, assume_yes: bool, purge: bool) -> int:
    manifest = load_json(paths.manifest_file, None)
    if not manifest:
        print("Research Peer is already uninstalled; no install manifest was found.")
        return 0
    if manifest.get("product") != "research-peer" or Path(manifest.get("home", "")) != paths.home:
        raise InstallSafetyError("install manifest identity/home mismatch")
    program_items = [item for item in manifest["items"] if item.get("category") == "program"]
    data_dirs = [paths.config_dir, paths.state_dir, paths.cache_dir, paths.runtime_dir]
    plan = {
        "remove": _display_program_targets(paths, program_items),
        "owned_program_entries": len(program_items),
        "purge": [str(path) for path in data_dirs] if purge else [],
        "preserve": ["project repositories", "experiment artifacts", "unrelated Claude settings/plugins/skills", "remote peer data"],
        "data_policy": "remove all Research Peer-owned local identity, rooms, history, outbox, logs and cache" if purge else "preserve Research Peer state because --keep-data was specified",
    }
    print("Research Peer uninstall plan:")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if dry_run:
        print("Dry run only; nothing changed.")
        return 0
    if purge:
        print("WARNING: uninstall removes this local Research Peer identity and pairing continuity. Use --keep-data to preserve it.")
    if not assume_yes:
        if not sys.stdin.isatty():
            print("Refusing non-interactive uninstall without --yes.", file=sys.stderr)
            return 2
        answer = input("Proceed with this local uninstall plan? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            print("Uninstall cancelled; nothing changed.")
            return 1

    if _systemd_allowed(paths):
        subprocess.run(["systemctl", "--user", "stop", "research-peer.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["systemctl", "--user", "disable", "research-peer.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _stop_owned_pid(paths)
    _remove_program_items(manifest)
    allowed = {Path(value) for value in manifest.get("exclusive_directories", [])}
    for directory in (
        paths.data_dir / "app",
        paths.home / ".claude/skills/research-peer-plugin",
        paths.home / ".claude/skills/research-peer",
    ):
        _safe_remove_exclusive(directory, allowed, paths.home)
    if purge:
        for directory in sorted(data_dirs, key=lambda value: len(str(value)), reverse=True):
            _safe_remove_exclusive(directory, allowed, paths.home, paths.runtime_dir)
        try:
            paths.manifest_file.unlink()
        except FileNotFoundError:
            pass
        try:
            paths.data_dir.rmdir()
        except OSError:
            pass
    else:
        try:
            paths.manifest_file.unlink()
        except FileNotFoundError:
            pass
    if _systemd_allowed(paths):
        subprocess.run(["systemctl", "--user", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    residue = residue_scan(paths)
    print("\nResearch Peer uninstalled successfully.\n")
    print("Removed:\n  CLI\n  Claude plugin\n  /research-peer skill\n  user service\n  runtime process")
    print("\nPreserved:\n  project repositories\n  experiment artifacts\n  unrelated Claude settings\n  remote peer data")
    if not purge:
        print("  local Research Peer state and identity (--keep-data)")
    print("\nRemaining Research Peer files:")
    remaining = [path for path in residue if path.exists() or path.is_symlink()]
    if remaining:
        for path in remaining:
            print(f"  {path}")
    else:
        print("  none")
    return 0


def _display_program_targets(paths: Paths, items: list[dict[str, Any]]) -> list[str]:
    roots = [
        paths.home / ".local/bin/research-peer",
        paths.home / ".local/bin/rp",
        paths.home / ".claude/skills/research-peer",
        paths.home / ".claude/skills/research-peer-plugin",
        paths.home / ".config/systemd/user/research-peer.service",
        paths.data_dir / "app",
        paths.manifest_file,
    ]
    owned = {Path(item["path"]) for item in items}
    display = []
    for root in roots:
        count = sum(1 for path in owned if path == root or root in path.parents)
        if count:
            suffix = f" ({count} owned entries)" if count > 1 else ""
            display.append(f"{root}{suffix}")
    return display


def _stop_owned_pid(paths: Paths) -> None:
    if not paths.pid_file.exists():
        return
    try:
        pid = int(paths.pid_file.read_text().strip())
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ")
        if b"research_peer" in cmdline or b"research-peer" in cmdline:
            os.kill(pid, 15)
    except (ValueError, FileNotFoundError, ProcessLookupError, PermissionError):
        pass
    paths.pid_file.unlink(missing_ok=True)
    (paths.runtime_dir / "daemon.ready").unlink(missing_ok=True)
    paths.socket_file.unlink(missing_ok=True)


def residue_scan(paths: Paths) -> list[Path]:
    return [
        paths.home / ".local/bin/research-peer",
        paths.home / ".local/bin/rp",
        paths.home / ".claude/skills/research-peer",
        paths.home / ".claude/skills/research-peer-plugin",
        paths.home / ".config/systemd/user/research-peer.service",
        paths.data_dir / "app", paths.pid_file, paths.socket_file,
        paths.config_dir, paths.state_dir, paths.cache_dir,
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research-peer-installer")
    parser.add_argument("command", choices=["install"])
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args(argv)
    try:
        result = install(Path(args.source))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, RuntimeError) as exc:
        print(f"install failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
