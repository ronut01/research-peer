from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Sequence

from . import __version__
from .paths import Paths, load_json


OFFICIAL_REPOSITORY = "https://github.com/ronut01/research-peer.git"
OFFICIAL_REPOSITORY_PAGE = "https://github.com/ronut01/research-peer"
_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.-]+))?$")


class UpdateError(RuntimeError):
    pass


def _run(command: Sequence[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(command),
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        tail = detail[-1][:500] if detail else f"exit status {result.returncode}"
        raise UpdateError(f"{Path(command[0]).name} failed: {tail}")
    return result


def _version_key(value: str) -> tuple[int, int, int, int, str]:
    match = _VERSION_PATTERN.fullmatch(value)
    if not match:
        raise UpdateError(f"invalid release version: {value!r}")
    major, minor, patch = (int(match.group(index)) for index in range(1, 4))
    suffix = match.group(4)
    return major, minor, patch, 1 if suffix is None else 0, suffix or ""


def _required_regular_file(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
        raise UpdateError(f"official checkout is missing a regular {relative}")
    return path


def inspect_release(root: Path) -> dict[str, Any]:
    root = root.resolve()
    required = [
        "install.sh",
        "src/research_peer/__init__.py",
        "src/research_peer/installer.py",
        "channel/research-peer-channel.mjs",
        "channel/security.mjs",
        "plugin/.claude-plugin/plugin.json",
        "plugin/.mcp.json",
        "plugin/skills/update/SKILL.md",
        "skill/SKILL.md",
        "pyproject.toml",
        "package.json",
        "package-lock.json",
        ".claude-plugin/marketplace.json",
    ]
    files = {relative: _required_regular_file(root, relative) for relative in required}

    init_text = files["src/research_peer/__init__.py"].read_text(encoding="utf-8")
    init_match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', init_text, re.MULTILINE)
    if not init_match:
        raise UpdateError("official checkout has no package version")
    pyproject_text = files["pyproject.toml"].read_text(encoding="utf-8")
    project_match = re.search(r'^version\s*=\s*["\']([^"\']+)["\']\s*$', pyproject_text, re.MULTILINE)
    if not project_match:
        raise UpdateError("official checkout has no project version")

    package = json.loads(files["package.json"].read_text(encoding="utf-8"))
    package_lock = json.loads(files["package-lock.json"].read_text(encoding="utf-8"))
    plugin = json.loads(files["plugin/.claude-plugin/plugin.json"].read_text(encoding="utf-8"))
    marketplace = json.loads(files[".claude-plugin/marketplace.json"].read_text(encoding="utf-8"))
    marketplace_plugins = [item for item in marketplace.get("plugins", []) if item.get("name") == "research-peer"]
    if plugin.get("name") != "research-peer" or len(marketplace_plugins) != 1:
        raise UpdateError("official checkout has an unexpected plugin identity")

    versions = {
        init_match.group(1),
        project_match.group(1),
        str(package.get("version", "")),
        str(package_lock.get("version", "")),
        str(package_lock.get("packages", {}).get("", {}).get("version", "")),
        str(plugin.get("version", "")),
        str(marketplace_plugins[0].get("version", "")),
    }
    if len(versions) != 1:
        raise UpdateError(f"official checkout has inconsistent component versions: {sorted(versions)!r}")
    version = versions.pop()
    _version_key(version)
    return {"version": version, "plugin": plugin["name"]}


def _repository_url() -> str:
    if os.environ.get("RESEARCH_PEER_TESTING") == "1":
        return os.environ.get("RESEARCH_PEER_UPDATE_REPOSITORY", OFFICIAL_REPOSITORY)
    return OFFICIAL_REPOSITORY


def _clone_official(destination: Path) -> dict[str, str]:
    git = shutil.which("git")
    if git is None:
        raise UpdateError("git is required for Research Peer updates")
    repository = _repository_url()
    _run([git, "clone", "--depth", "1", "--", repository, str(destination)])
    origin = _run([git, "-C", str(destination), "remote", "get-url", "origin"]).stdout.strip()
    if origin != repository:
        raise UpdateError("cloned repository origin does not match the trusted update source")
    commit = _run([git, "-C", str(destination), "rev-parse", "--verify", "HEAD"]).stdout.strip()
    branch = _run([git, "-C", str(destination), "rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise UpdateError("official checkout returned an invalid Git commit")
    return {"commit": commit, "branch": branch}


def _daemon_running(paths: Paths) -> bool:
    if not paths.pid_file.exists():
        return False
    try:
        pid = int(paths.pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return True
    except (ValueError, FileNotFoundError, ProcessLookupError, PermissionError):
        return False


def _installed_manifest(paths: Paths) -> dict[str, Any]:
    manifest = load_json(paths.manifest_file, None)
    if not manifest:
        raise UpdateError("no Research Peer install manifest was found; use ./install.sh from a reviewed checkout")
    if manifest.get("product") != "research-peer" or Path(manifest.get("home", "")) != paths.home:
        raise UpdateError("install manifest identity/home mismatch")
    manifest_version = str(manifest.get("version", ""))
    if manifest_version != __version__:
        raise UpdateError(
            f"installed package ({__version__}) and manifest ({manifest_version or 'missing'}) versions differ; "
            "use ./install.sh from a reviewed checkout"
        )
    return manifest


def _confirm_update(current: str, latest: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise UpdateError("refusing non-interactive update without --yes")
    answer = input(f"Update Research Peer {current} -> {latest} from the official GitHub repository? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _stop_for_update() -> None:
    _run([sys.executable, "-m", "research_peer", "stop"])


def _install_checkout(checkout: Path) -> None:
    shell = shutil.which("sh")
    if shell is None:
        raise UpdateError("a POSIX shell is required to run the reviewed installer")
    _run([shell, str(checkout / "install.sh")], cwd=checkout)


def _restart_after_update(paths: Paths) -> None:
    cli = paths.home / ".local/bin/research-peer"
    if not cli.is_file() or cli.is_symlink():
        raise UpdateError("updated Research Peer launcher is missing")
    _run([str(cli), "start", "--daemon-only"])


def update(*, check_only: bool, assume_yes: bool, paths: Paths | None = None) -> dict[str, Any]:
    paths = paths or Paths.discover()
    _installed_manifest(paths)
    with tempfile.TemporaryDirectory(prefix="research-peer-update-") as temporary:
        checkout = Path(temporary) / "checkout"
        revision = _clone_official(checkout)
        release = inspect_release(checkout)
        current = __version__
        latest = release["version"]
        comparison = (_version_key(latest) > _version_key(current)) - (_version_key(latest) < _version_key(current))
        plan = {
            "source": OFFICIAL_REPOSITORY_PAGE,
            "commit": revision["commit"],
            "branch": revision["branch"],
            "current_version": current,
            "latest_version": latest,
            "preserve": [
                "identity and private key",
                "rooms, peers, history, and pending outbox",
                "configuration and logs",
                "project repositories and experiment artifacts",
            ],
        }
        if comparison < 0:
            raise UpdateError(f"refusing downgrade from {current} to {latest}")
        if comparison == 0:
            return {**plan, "updated": False, "up_to_date": True}
        if check_only:
            return {**plan, "updated": False, "update_available": True}
        if not _confirm_update(current, latest, assume_yes):
            return {**plan, "updated": False, "cancelled": True}

        daemon_was_running = _daemon_running(paths)
        if daemon_was_running:
            _stop_for_update()
        try:
            _install_checkout(checkout)
            installed = _run([str(paths.home / ".local/bin/research-peer"), "version"]).stdout.strip()
            if installed != latest:
                raise UpdateError(f"post-update version check failed: expected {latest}, got {installed or 'no output'}")
        except Exception:
            if daemon_was_running:
                try:
                    _restart_after_update(paths)
                except Exception:
                    pass
            raise
        if daemon_was_running:
            _restart_after_update(paths)
        return {
            **plan,
            "updated": True,
            "up_to_date": True,
            "daemon_restarted": daemon_was_running,
            "state_preserved": True,
            "next_step": "Restart this Claude session to load the updated development Channel and skills.",
        }
