#!/usr/bin/env python3
"""Fail if PYODIDE_VERSION in .github/pyodide-versions.env drifts from worker.js."""

from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = REPO_ROOT / ".github" / "pyodide-versions.env"
WORKER_FILE = REPO_ROOT / "worker.js"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def parse_worker_pyodide_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")

    # Find the PYODIDE_CDN assignment first, then extract the version from its URL.
    cdn_assign_match = re.search(r"const\s+PYODIDE_CDN\s*=\s*['\"]([^'\"]+)['\"]\s*;", text)
    if not cdn_assign_match:
        raise ValueError("Could not find PYODIDE_CDN in worker.js")

    pyodide_cdn_url = cdn_assign_match.group(1)
    version_match = re.search(r"/pyodide/v([^/]+)/full/pyodide\.js$", pyodide_cdn_url)
    if not version_match:
        raise ValueError(f"PYODIDE_CDN has unexpected format: {pyodide_cdn_url}")

    return version_match.group(1)


def main() -> int:
    env_values = parse_env_file(ENV_FILE)
    env_version = env_values.get("PYODIDE_VERSION")
    if not env_version:
        print(f"error: PYODIDE_VERSION missing in {ENV_FILE}", file=sys.stderr)
        return 1

    worker_version = parse_worker_pyodide_version(WORKER_FILE)

    if env_version != worker_version:
        print(
            "error: Pyodide version mismatch:\n"
            f"  .github/pyodide-versions.env: PYODIDE_VERSION={env_version}\n"
            f"  worker.js: PYODIDE_CDN uses v{worker_version}\n"
            "Please update both to match.",
            file=sys.stderr,
        )
        return 1

    print(f"ok: Pyodide version is synced ({env_version}) between env file and worker.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
