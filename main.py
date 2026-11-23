"""Entry point: connect to M1 + M2, then run the interactive command shell.

CRUD logic lives in crud/ (teammate 1) and crud2/ (teammate 2).
"""

import shlex

# db must be imported first (gevent monkey-patch for Cassandra on Python 3.12+)
import db  # noqa: F401
from db import connect_all

from crud import COMMANDS as CRUD1
from crud2 import COMMANDS as CRUD2
import integrity
import failure
import demo

COMMANDS = {
    **CRUD1,
    **CRUD2,
    integrity.NAME: integrity,
    failure.NAME: failure,
    demo.NAME: demo,
}


def print_help():
    print("\nCommands:")
    for name, module in COMMANDS.items():
        print(f"  {module.USAGE:<28} {module.HELP}")
    print(f"  {'help':<28} Show this help")
    print(f"  {'exit / quit':<28} Close connections and leave")


def handle_command(line):
    """Run one interactive command. Returns False when the shell should stop."""
    try:
        parts = shlex.split(line)
    except ValueError as e:
        print(f"Could not parse command: {e}")
        return True
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
    connect_all()
    interactive_loop()


if __name__ == "__main__":
    main()
