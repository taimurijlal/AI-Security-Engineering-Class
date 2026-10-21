"""
GlobalTech AI — unified CLI entry point.

Run with:
    python -m app.main
"""

from __future__ import annotations

from app.core.cli_shell import run_module
from app.registry import MODULES


def choose_module():
    print("\nChoose a lab module:")
    for i, spec in enumerate(MODULES, start=1):
        print(f"  {i}. {spec.nav_label}")
    choice = input("\nSelection [1]: ").strip() or "1"
    try:
        index = int(choice) - 1
    except ValueError:
        index = 0
    if index < 0 or index >= len(MODULES):
        index = 0
    return MODULES[index]


def main() -> None:
    spec = choose_module()
    run_module(spec)


if __name__ == "__main__":
    main()
