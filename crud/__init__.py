"""Teammate 1 CRUD: doctor–patient visits."""

from . import read_doctor_patient
from . import insert
from . import update
from . import delete

COMMAND_MODULES = (
    read_doctor_patient,
    insert,
    update,
    delete,
)

COMMANDS = {module.NAME: module for module in COMMAND_MODULES}
