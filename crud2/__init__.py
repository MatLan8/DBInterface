"""Teammate 2 CRUD: medical-procedure visits (+ claim/payment)."""

from . import read_rx_payment
from . import insert
from . import update
from . import delete

COMMAND_MODULES = (
    read_rx_payment,
    insert,
    update,
    delete,
)

COMMANDS = {module.NAME: module for module in COMMAND_MODULES}
