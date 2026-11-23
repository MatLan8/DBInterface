"""UPDATE: raise prescription dosage and adjust payment / visit / claim amounts."""

import time
from collections import namedtuple
from decimal import Decimal, InvalidOperation

import psycopg2.extras

import db

NAME = "update_rx"
USAGE = (
    'update_rx <visit_id> <prescription_id> "<new_dosage>" '
    "<paid_amount> <claim_covered>"
)
HELP = (
    'Update prescription dosage (e.g. "2 drops") and add to '
    "payment / visit total / claim (requires insurance_covered)"
)

_Prescription = namedtuple(
    "prescription_udt", "prescription_id medication_name dosage valid_until"
)
_Lab = namedtuple(
    "lab_result_udt", "lab_result_id test_name result_value test_date"
)
_Procedure = namedtuple(
    "medical_procedure_udt", "procedure_id procedure_name category performed_at"
)


def _register_udts(session, keyspace):
    cluster = session.cluster
    cluster.register_user_type(keyspace, "prescription_udt", _Prescription)
    cluster.register_user_type(keyspace, "lab_result_udt", _Lab)
    cluster.register_user_type(keyspace, "medical_procedure_udt", _Procedure)


def _as_date(value):
    if value is None:
        return None
    if hasattr(value, "date") and not hasattr(value, "year"):
        return value.date()
    return value


def _money(value, name):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"{name} must be a number") from e
    if amount <= 0:
        raise ValueError(f"{name} must be a positive number")
    return amount.quantize(Decimal("0.01"))


def _find_visit(visit_id):
    with db.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for frag in db.PG_FRAGMENTS:
            cur.execute(
                f"SELECT * FROM {frag['schema']}.visit WHERE visit_id = %s",
                (visit_id,),
            )
            row = cur.fetchone()
            if row:
                return frag, row
    return None, None


def update_rx(visit_id, prescription_id, new_dosage, paid_amount, claim_covered):
    t0 = time.perf_counter()
    rows = 0

    if paid_amount < claim_covered:
        print(
            f"Error: paid_amount ({paid_amount}) must be >= "
            f"claim_covered ({claim_covered})."
        )
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    frag, visit = _find_visit(visit_id)
    if not frag:
        print(f"Error: visit_id={visit_id} not found in db11 or db12.")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    if not visit["insurance_covered"]:
        print(
            f"Error: visit_id={visit_id} has insurance_covered=false, "
            f"but claim_covered={claim_covered} was provided. "
            f"Update only allowed when insurance_covered=true."
        )
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    schema, ks = frag["schema"], frag["cassandra"]
    session = db.cassandra_sessions[ks]
    cass = session.execute(
        "SELECT * FROM visit WHERE visit_id = %s", (visit_id,)
    ).one()
    if not cass:
        print(
            f"Error: visit_id={visit_id} missing in Cassandra {ks} "
            f"(Postgres found in {schema})."
        )
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    prescriptions = list(cass.prescriptions or [])
    idx = next(
        (
            i
            for i, p in enumerate(prescriptions)
            if p.prescription_id == prescription_id
        ),
        None,
    )
    if idx is None:
        print(
            f"Error: prescription_id={prescription_id} not found "
            f"on visit_id={visit_id}."
        )
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    with db.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT payment_id, amount_paid FROM {schema}.payment "
            f"WHERE visit_id = %s ORDER BY payment_id LIMIT 1",
            (visit_id,),
        )
        payment = cur.fetchone()
        cur.execute(
            f"SELECT claim_id, claim_amount FROM {schema}.insurance_claim "
            f"WHERE visit_id = %s ORDER BY claim_id LIMIT 1",
            (visit_id,),
        )
        claim = cur.fetchone()

    if not payment:
        print(f"Error: no payment row for visit_id={visit_id} in {schema}.")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return
    if not claim:
        print(f"Error: no insurance_claim row for visit_id={visit_id} in {schema}.")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    old = prescriptions[idx]
    updated = []
    for i, p in enumerate(prescriptions):
        dosage = new_dosage if i == idx else p.dosage
        updated.append(
            _Prescription(
                p.prescription_id,
                p.medication_name,
                dosage,
                _as_date(p.valid_until),
            )
        )

    _register_udts(session, ks)
    new_paid = payment["amount_paid"] + paid_amount
    new_total = visit["total_amount_eur"] + paid_amount
    new_claim = claim["claim_amount"] + claim_covered

    try:
        with db.pg_conn.cursor() as cur:
            session.execute(
                "UPDATE visit SET prescriptions = %s WHERE visit_id = %s",
                (updated, visit_id),
            )
            rows += 1

            cur.execute(
                f"UPDATE {schema}.payment SET amount_paid = %s "
                f"WHERE payment_id = %s",
                (new_paid, payment["payment_id"]),
            )
            rows += cur.rowcount

            cur.execute(
                f"UPDATE {schema}.visit SET total_amount_eur = %s "
                f"WHERE visit_id = %s",
                (new_total, visit_id),
            )
            rows += cur.rowcount

            cur.execute(
                f"UPDATE {schema}.insurance_claim SET claim_amount = %s "
                f"WHERE claim_id = %s",
                (new_claim, claim["claim_id"]),
            )
            rows += cur.rowcount

        db.pg_conn.commit()
    except Exception as e:
        db.pg_conn.rollback()
        print(f"Error: update failed — {e}")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    ms = (time.perf_counter() - t0) * 1000
    print(
        f"Updated visit_id={visit_id} in {schema}+{ks}: "
        f"prescription {prescription_id} dosage "
        f"{old.dosage!r} → {new_dosage!r}."
    )
    print(
        f"payment.amount_paid {payment['amount_paid']} → {new_paid}; "
        f"visit.total_amount_eur {visit['total_amount_eur']} → {new_total}; "
        f"claim.claim_amount {claim['claim_amount']} → {new_claim}."
    )
    print(f"Rows updated: {rows}")
    print(f"Time: {ms:.2f} ms")


def run(args):
    if len(args) != 5:
        print(f"Usage: {USAGE}")
        return
    try:
        visit_id = int(args[0])
        prescription_id = int(args[1])
        new_dosage = str(args[2]).strip()
        if not new_dosage:
            raise ValueError('new_dosage must be a non-empty string (e.g. "2 drops")')
        paid_amount = _money(args[3], "paid_amount")
        claim_covered = _money(args[4], "claim_covered")
    except ValueError as e:
        print(f"Error: {e}")
        return
    update_rx(visit_id, prescription_id, new_dosage, paid_amount, claim_covered)
