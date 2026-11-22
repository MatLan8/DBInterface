"""Entry point: connect to M1 + M2, then run the interactive command shell.

CRUD logic lives in crud/*.py — register modules in crud/__init__.py.
"""

# db must be imported first (gevent monkey-patch for Cassandra on Python 3.12+)
import db  # noqa: F401
from db import connect_all

import argparse

from crud import COMMANDS
from crud.read import read_visit
from db import print_json


def parse_args():
    parser = argparse.ArgumentParser(
        description="Heterogeneous distributed DB demo (M1 Postgres + M2 Cassandra)."
    )
    parser.add_argument(
        "--read",
        type=int,
        metavar="VISIT_ID",
        help="One-shot global read, then exit (optional; otherwise interactive mode).",
    )
    return parser.parse_args()


def print_help():
    print("\nCommands:")
    for name, module in COMMANDS.items():
        print(f"  {module.USAGE:<22} {module.HELP}")
    print(f"  {'help':<22} Show this help")
    print(f"  {'exit / quit':<22} Close connections and leave")


def handle_command(line):
    """Run one interactive command. Returns False when the shell should stop."""
    parts = line.split()
    if not parts:
        return True

    cmd = parts[0].lower()

    if cmd in ("exit", "quit", "q"):
        return False

    if cmd in ("help", "h", "?"):
        print_help()
        return True

    module = COMMANDS.get(cmd)
    if module is None:
        print(f"Unknown command: {cmd!r}. Type 'help' for available commands.")
        return True

    module.run(parts[1:])
    return True


def interactive_loop():
    print_help()
    print("\nConnected. Enter a command.")
    while True:
        try:
            line = input("db> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not handle_command(line):
            break


def main():
    args = parse_args()
    connect_all()

    if args.read is not None:
        print(f"\n=== Global READ visit_id={args.read} ===")
        result = read_visit(args.read)
        print_json(result)
        if not result["found"]:
            raise SystemExit(1)
        return

    interactive_loop()


if __name__ == "__main__":
    main()
