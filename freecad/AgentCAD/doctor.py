"""Operator-facing readiness checks for Agent Workbench for FreeCAD."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from . import LEGACY_ID, MANIFEST_NAME, PRODUCT_NAME, SHORT_NAME, __version__
from .providers.codex import CodexProvider

MINIMUM_PYTHON = (3, 11)


@dataclass(frozen=True)
class CheckResult:
    """A single diagnostic result with machine-readable status."""

    check: str
    label: str
    status: str
    message: str


def run_checks(*, skip_codex: bool = False) -> dict[str, object]:
    """Run workbench readiness checks without exposing credential material."""
    checks = [
        _check_python(),
        _check_package(),
        _check_manifest(),
        _check_data_directory(),
    ]
    checks.append(_skipped_codex() if skip_codex else _check_codex())
    failed = sum(item.status == "fail" for item in checks)
    warnings = sum(item.status == "warning" for item in checks)
    passed = sum(item.status == "pass" for item in checks)
    return {
        "product": PRODUCT_NAME,
        "version": __version__,
        "ok": failed == 0,
        "checks": [asdict(item) for item in checks],
        "summary": {
            "passed": passed,
            "warnings": warnings,
            "failed": failed,
            "skipped": sum(item.status == "skipped" for item in checks),
        },
    }


def _check_python() -> CheckResult:
    current = sys.version_info[:3]
    required = ".".join(str(item) for item in MINIMUM_PYTHON)
    actual = ".".join(str(item) for item in current)
    if current < MINIMUM_PYTHON:
        return CheckResult(
            "python",
            "Python runtime",
            "fail",
            f"Python {actual} is too old. {SHORT_NAME} requires Python {required} or newer.",
        )
    return CheckResult(
        "python",
        "Python runtime",
        "pass",
        f"Python {actual} on {platform.system()} is supported.",
    )


def _check_package() -> CheckResult:
    try:
        version = str(__version__).strip()
        if not version:
            raise RuntimeError("package version is empty")
        from .core.tools import ToolRegistry
        from .providers.registry import ProviderRegistry

        ToolRegistry()
        ProviderRegistry()
    except Exception as exc:
        return CheckResult(
            "package",
            f"{SHORT_NAME} package",
            "fail",
            f"{SHORT_NAME} package import failed: {_safe_error(exc)}",
        )
    return CheckResult(
        "package",
        f"{SHORT_NAME} package",
        "pass",
        f"{SHORT_NAME} {version} imports successfully.",
    )


def _check_manifest() -> CheckResult:
    path = _repository_root() / "package.xml"
    if not path.is_file():
        return CheckResult(
            "manifest",
            "FreeCAD manifest",
            "fail",
            "package.xml was not found next to the workbench source tree.",
        )
    try:
        root = ET.parse(path).getroot()
        values = {_local_name(child.tag): (child.text or "").strip() for child in root}
        content = next((child for child in root if _local_name(child.tag) == "content"), None)
        workbench = content is not None and any(
            _local_name(child.tag) == "workbench" for child in content
        )
        if values.get("name") != MANIFEST_NAME:
            raise ValueError(f"manifest name must be {MANIFEST_NAME}")
        if values.get("version") != __version__:
            raise ValueError("manifest version does not match the package version")
        if not values.get("freecadmin"):
            raise ValueError("minimum FreeCAD version is missing")
        if not workbench:
            raise ValueError("FreeCAD workbench content declaration is missing")
    except (ET.ParseError, OSError, ValueError) as exc:
        return CheckResult(
            "manifest",
            "FreeCAD manifest",
            "fail",
            f"package.xml is invalid: {_safe_error(exc)}",
        )
    return CheckResult(
        "manifest",
        "FreeCAD manifest",
        "pass",
        f"FreeCAD package metadata is valid for {SHORT_NAME} {__version__}.",
    )


def _check_data_directory() -> CheckResult:
    directory = _data_directory()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=".agentcad-doctor-",
            dir=directory,
            delete=True,
        ) as stream:
            stream.write(f"{SHORT_NAME} write check\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        return CheckResult(
            "data_directory",
            "Local data directory",
            "fail",
            f"{SHORT_NAME} cannot write its local data directory: {_safe_error(exc)}",
        )
    return CheckResult(
        "data_directory",
        "Local data directory",
        "pass",
        f"{SHORT_NAME} can write its project-scoped local data directory.",
    )


def _check_codex() -> CheckResult:
    try:
        status = CodexProvider().status()
    except Exception as exc:
        return CheckResult(
            "codex",
            "OpenAI Codex connection",
            "fail",
            f"Codex readiness check failed: {_safe_error(exc)}",
        )
    if not status.available:
        return CheckResult("codex", "OpenAI Codex connection", "fail", status.message)
    if not status.authenticated:
        return CheckResult("codex", "OpenAI Codex connection", "fail", status.message)
    version = f" ({status.version})" if status.version else ""
    return CheckResult(
        "codex",
        "OpenAI Codex connection",
        "pass",
        f"Codex is installed and connected{version}.",
    )


def _skipped_codex() -> CheckResult:
    return CheckResult(
        "codex",
        "OpenAI Codex connection",
        "skipped",
        "Codex executable and login checks were skipped by request.",
    )


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _data_directory() -> Path:
    override = os.environ.get("AGENT_WORKBENCH_DATA_DIR") or os.environ.get(
        "AGENTCAD_DATA_DIR"
    )
    if override:
        return Path(override).expanduser()
    try:
        import FreeCAD as App

        root = str(App.getUserAppDataDir()).strip()
        if root:
            return Path(root).expanduser() / LEGACY_ID
    except (ImportError, AttributeError, RuntimeError):
        pass
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / LEGACY_ID
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            return Path(local) / LEGACY_ID
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / "agentcad"
    return Path.home() / ".local" / "share" / "agentcad"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _safe_error(error: BaseException) -> str:
    message = str(error).strip().replace("\n", " ")
    return message[:300] or error.__class__.__name__


def _render_human(report: dict[str, object]) -> str:
    rows = [f"{SHORT_NAME} Doctor {report['version']}", ""]
    labels = {"pass": "PASS", "fail": "FAIL", "warning": "WARN", "skipped": "SKIP"}
    for item in report["checks"]:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", "fail"))
        rows.append(
            f"[{labels.get(status, 'FAIL')}] {item.get('label')}: {item.get('message')}"
        )
    summary = report["summary"]
    if isinstance(summary, dict):
        rows.extend(
            [
                "",
                "Ready." if report["ok"] else "Not ready.",
                (
                    f"{summary.get('passed', 0)} passed, {summary.get('failed', 0)} failed, "
                    f"{summary.get('skipped', 0)} skipped."
                ),
            ]
        )
    return "\n".join(rows)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-workbench-doctor",
        description=f"Check whether {SHORT_NAME} and its Codex connection are ready.",
    )
    parser.add_argument("--json", action="store_true", help="Print a JSON readiness report.")
    parser.add_argument(
        "--skip-codex",
        action="store_true",
        help="Skip the Codex executable and login checks.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_checks(skip_codex=args.skip_codex)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_render_human(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
