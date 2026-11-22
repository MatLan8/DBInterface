"""CRUD command modules. Each file exports NAME, HELP, USAGE, and run(args)."""

from . import read
from . import insert
from . import update
from . import delete

# Register interactive commands here as you implement them
COMMAND_MODULES = (
    read,
    insert,
    update,
    delete,
)

COMMANDS = {module.NAME: module for module in COMMAND_MODULES}
