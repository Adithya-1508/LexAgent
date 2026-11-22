#!/usr/bin/env python3
"""Pre-push quality gate: type checking and legal safety invariants."""

import subprocess
import sys


def main() -> None:
    # 1. Type analysis across source codebase
    res_mypy = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "--offline",
            "mypy",
            "--config-file",
            "pyproject.toml",
            "src/",
        ]
    )

    # 2. Statutory invariant legal safety tests (always executes even if mypy fails)
    res_pytest = subprocess.run(
        [
            "uv",
            "run",
            "--no-sync",
            "--offline",
            "pytest",
            "-c",
            "pyproject.toml",
            "-q",
            "-m",
            "legal_safety",
        ]
    )

    # Determine overall exit code: exit non-zero if either check failed
    if res_mypy.returncode != 0:
        sys.exit(res_mypy.returncode)
    if res_pytest.returncode != 0:
        sys.exit(res_pytest.returncode)


if __name__ == "__main__":
    main()
