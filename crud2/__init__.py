"""Teammate 2 CRUD: medical-procedure visits (+ claim/payment)."""

from . import read_rx_payment
from . import insert
from . import update
from . import delete
from . import fn_lwt_insert_doctor
from . import fn_ttl_update_visit

COMMAND_MODULES = (
    read_rx_payment,
    insert,
    update,
    delete,
    fn_lwt_insert_doctor,
    fn_ttl_update_visit,
)

COMMANDS = {module.NAME: module for module in COMMAND_MODULES}
