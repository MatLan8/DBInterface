"""LWT: insert a doctor only when the doctor_id is not already present."""

import time

import db

NAME = "lwt_doctor"
USAGE = "lwt_doctor <region> <doctor_id> <full_name>"
HELP = "Insert a doctor only if doctor_id does not already exist (Vilnius/Kaunas)"


def insert_doctor_if_not_exists(session, doctor_id, full_name):
    row = session.execute(
        """
        INSERT INTO doctor (doctor_id, full_name)
        VALUES (%s, %s)
        IF NOT EXISTS
        """,
        (doctor_id, full_name),
    ).one()
    return bool(row and row.applied)


def run(args):
    if len(args) != 3:
        print(f"Usage: {USAGE}")
        return

    region, doctor_id, full_name = args
    try:
        doctor_id = int(doctor_id)
        full_name = full_name.strip()
        if not full_name:
            raise ValueError("full_name must not be empty")
    except ValueError as e:
        print(f"Error: invalid argument — {e}")
        return

    fragment = db.resolve_fragment(region)
    if not fragment:
        print(f"Error: unknown region {region!r}. Use Vilnius or Kaunas.")
        return

    t0 = time.perf_counter()
    session = db.cassandra_sessions[fragment["cassandra"]]
    try:
        applied = insert_doctor_if_not_exists(session, doctor_id, full_name)
    except Exception as e:
        print(f"Error: doctor insert failed — {e}")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    if applied:
        print(f"Inserted doctor_id={doctor_id} into {fragment['cassandra']}.")
    else:
        print(f"doctor_id={doctor_id} already exists in {fragment['cassandra']}; unchanged.")
    print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
