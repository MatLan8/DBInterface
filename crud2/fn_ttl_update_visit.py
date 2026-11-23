"""TTL: replace a visit's prescriptions with a value that expires."""

import time
from collections import namedtuple

import db

NAME = "ttl_visit"
USAGE = "ttl_visit <visit_id> <ttl_seconds>"
HELP = "Set a TTL on a visit's prescriptions (finds visit in db21/db22)"

_Prescription = namedtuple(
    "prescription_udt", "prescription_id medication_name dosage valid_until"
)


def _as_date(value):
    if value is None:
        return None
    if hasattr(value, "date") and not hasattr(value, "year"):
        return value.date()
    return value


def _convert_prescriptions(items):
    return [
        _Prescription(
            p.prescription_id,
            p.medication_name,
            p.dosage,
            _as_date(p.valid_until),
        )
        for p in items
    ]


def update_visit_prescriptions_with_ttl(
    session,
    keyspace,
    visit_id,
    prescriptions,
    ttl_seconds,
):
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise ValueError("ttl_seconds must be a positive integer")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be greater than zero")
    if prescriptions is None:
        raise ValueError("visit has no prescriptions value to expire")

    session.cluster.register_user_type(keyspace, "prescription_udt", _Prescription)
    converted = _convert_prescriptions(prescriptions)

    # TTL must be a literal in CQL; prescriptions/visit_id are bound params.
    prepared = session.prepare(
        f"""
        UPDATE visit USING TTL {ttl_seconds}
        SET prescriptions = ?
        WHERE visit_id = ?
        IF EXISTS
        """
    )
    result = session.execute(prepared, (converted, visit_id)).one()
    return bool(result and result.applied)


def _find_visit(visit_id):
    """Return (keyspace, session, row) or (None, None, None)."""
    for frag in db.PG_FRAGMENTS:
        ks = frag["cassandra"]
        session = db.cassandra_sessions[ks]
        row = session.execute(
            "SELECT prescriptions FROM visit WHERE visit_id = %s",
            (visit_id,),
        ).one()
        if row is not None:
            return ks, session, row
    return None, None, None


def run(args):
    if len(args) != 2:
        print(f"Usage: {USAGE}")
        return

    try:
        visit_id = int(args[0])
        ttl_seconds = int(args[1])
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
    except ValueError as e:
        print(f"Error: invalid argument — {e}")
        return

    t0 = time.perf_counter()
    try:
        ks, session, row = _find_visit(visit_id)
        if row is None:
            print(f"Error: visit_id={visit_id} not found in db21 or db22.")
            print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
            return
        if row.prescriptions is None:
            print(f"Error: visit_id={visit_id} has no prescriptions value to expire.")
            print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
            return

        updated = update_visit_prescriptions_with_ttl(
            session,
            ks,
            visit_id,
            row.prescriptions,
            ttl_seconds,
        )
    except Exception as e:
        print(f"Error: TTL update failed — {e}")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    if updated:
        print(
            f"Set TTL={ttl_seconds}s on prescriptions for visit_id={visit_id} "
            f"in {ks}."
        )
    else:
        print(
            f"Error: visit_id={visit_id} was deleted before the TTL update "
            f"in {ks}."
        )
    print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
