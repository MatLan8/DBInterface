"""UPDATE: move a visit (+ related rows) to another horizontal fragment."""

import time
from collections import namedtuple

import psycopg2.extras

import db

NAME = "update_move"
USAGE = "update_move <visit_id> <region> <doctor_id>"
HELP = "Move visit + related rows to another region fragment (Vilnius/Kaunas)"

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
        # cassandra.util.Date
        return value.date()
    return value


def _convert_prescriptions(items):
    if not items:
        return []
    return [
        _Prescription(
            item.prescription_id,
            item.medication_name,
            item.dosage,
            _as_date(item.valid_until),
        )
        for item in items
    ]


def _convert_labs(items):
    if not items:
        return []
    return [
        _Lab(item.lab_result_id, item.test_name, item.result_value, item.test_date)
        for item in items
    ]


def _convert_procedures(items):
    if not items:
        return []
    return [
        _Procedure(
            item.procedure_id, item.procedure_name, item.category, item.performed_at
        )
        for item in items
    ]


def _find_visit(visit_id):
    """Return (fragment, visit_row) or (None, None)."""
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


def update_move(visit_id, new_region, new_doctor_id):
    t0 = time.perf_counter()
    rows = 0

    dst = db.resolve_fragment(new_region)
    if not dst:
        print(f"Error: region must be 'Vilnius' or 'Kaunas', got {new_region!r}.")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    src, visit = _find_visit(visit_id)
    if not src:
        print(f"Error: visit_id={visit_id} not found in db11 or db12.")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    old_region = visit["region"]
    if old_region == new_region:
        print(
            f"Visit {visit_id} is already in region {new_region!r} "
            f"({src['schema']}+{src['cassandra']}). Nothing to do."
        )
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    dst_session = db.cassandra_sessions[dst["cassandra"]]
    doctor = dst_session.execute(
        "SELECT doctor_id FROM doctor WHERE doctor_id = %s", (new_doctor_id,)
    ).one()
    if not doctor:
        print(
            f"Error: doctor_id={new_doctor_id} not found in "
            f"{dst['cassandra']} ({new_region})."
        )
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    src_schema, src_ks = src["schema"], src["cassandra"]
    dst_schema, dst_ks = dst["schema"], dst["cassandra"]
    src_session = db.cassandra_sessions[src_ks]
    patient_id = visit["patient_id"]

    with db.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT * FROM {src_schema}.patient_profile WHERE patient_id = %s",
            (patient_id,),
        )
        patient = cur.fetchone()
        if not patient:
            print(
                f"Error: patient_id={patient_id} missing in {src_schema} "
                f"for visit {visit_id}."
            )
            print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
            return

        cur.execute(
            f"SELECT * FROM {src_schema}.insurance_claim WHERE visit_id = %s",
            (visit_id,),
        )
        claims = cur.fetchall()
        cur.execute(
            f"SELECT * FROM {src_schema}.payment WHERE visit_id = %s",
            (visit_id,),
        )
        payments = cur.fetchall()

    cass = src_session.execute(
        "SELECT * FROM visit WHERE visit_id = %s", (visit_id,)
    ).one()
    if not cass:
        print(
            f"Error: visit_id={visit_id} missing in Cassandra {src_ks} "
            f"(Postgres found in {src_schema})."
        )
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    _register_udts(dst_session, dst_ks)

    try:
        with db.pg_conn.cursor() as cur:
            # patient_profile → destination (new region)
            cur.execute(
                f"SELECT 1 FROM {dst_schema}.patient_profile WHERE patient_id = %s",
                (patient_id,),
            )
            if cur.fetchone():
                cur.execute(
                    f"UPDATE {dst_schema}.patient_profile SET region = %s "
                    f"WHERE patient_id = %s",
                    (new_region, patient_id),
                )
                rows += cur.rowcount
            else:
                cur.execute(
                    f"""
                    INSERT INTO {dst_schema}.patient_profile
                        (patient_id, region, full_name, birth_date,
                         phone, insurance_policy_no)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        patient["patient_id"],
                        new_region,
                        patient["full_name"],
                        patient["birth_date"],
                        patient["phone"],
                        patient["insurance_policy_no"],
                    ),
                )
                rows += cur.rowcount

            cur.execute(
                f"""
                INSERT INTO {dst_schema}.visit
                    (visit_id, region, patient_id, payment_status,
                     insurance_covered, total_amount_eur)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    visit["visit_id"],
                    new_region,
                    visit["patient_id"],
                    visit["payment_status"],
                    visit["insurance_covered"],
                    visit["total_amount_eur"],
                ),
            )
            rows += cur.rowcount

            for c in claims:
                cur.execute(
                    f"""
                    INSERT INTO {dst_schema}.insurance_claim
                        (claim_id, visit_id, insurance_company, claim_amount)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (
                        c["claim_id"],
                        c["visit_id"],
                        c["insurance_company"],
                        c["claim_amount"],
                    ),
                )
                rows += cur.rowcount

            for p in payments:
                cur.execute(
                    f"""
                    INSERT INTO {dst_schema}.payment
                        (payment_id, visit_id, amount_paid, payment_method, paid_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        p["payment_id"],
                        p["visit_id"],
                        p["amount_paid"],
                        p["payment_method"],
                        p["paid_at"],
                    ),
                )
                rows += cur.rowcount

            # Cassandra visit → destination (new region + doctor)
            dst_session.execute(
                """
                INSERT INTO visit
                    (visit_id, region, doctor_id, visit_date, visit_location,
                     symptoms, diagnosis, prescriptions, lab_results, procedures)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    cass.visit_id,
                    new_region,
                    new_doctor_id,
                    cass.visit_date,
                    cass.visit_location,
                    cass.symptoms,
                    cass.diagnosis,
                    _convert_prescriptions(cass.prescriptions),
                    _convert_labs(cass.lab_results),
                    _convert_procedures(cass.procedures),
                ),
            )
            rows += 1

            # remove from source fragment
            cur.execute(
                f"DELETE FROM {src_schema}.payment WHERE visit_id = %s", (visit_id,)
            )
            rows += cur.rowcount
            cur.execute(
                f"DELETE FROM {src_schema}.insurance_claim WHERE visit_id = %s",
                (visit_id,),
            )
            rows += cur.rowcount
            cur.execute(
                f"DELETE FROM {src_schema}.visit WHERE visit_id = %s", (visit_id,)
            )
            rows += cur.rowcount
            src_session.execute(
                "DELETE FROM visit WHERE visit_id = %s", (visit_id,)
            )
            rows += 1

            cur.execute(
                f"SELECT 1 FROM {src_schema}.visit WHERE patient_id = %s LIMIT 1",
                (patient_id,),
            )
            if not cur.fetchone():
                cur.execute(
                    f"DELETE FROM {src_schema}.patient_profile WHERE patient_id = %s",
                    (patient_id,),
                )
                rows += cur.rowcount
            else:
                # other visits still in source — leave patient there
                pass

        db.pg_conn.commit()
    except Exception as e:
        db.pg_conn.rollback()
        print(f"Error: move failed — {e}")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    ms = (time.perf_counter() - t0) * 1000
    print(
        f"Moved visit_id={visit_id}: {old_region} ({src_schema}+{src_ks}) "
        f"→ {new_region} ({dst_schema}+{dst_ks}), doctor_id={new_doctor_id}."
    )
    print(f"Rows updated: {rows}")
    print(f"Time: {ms:.2f} ms")


def run(args):
    if len(args) != 3:
        print(f"Usage: {USAGE}")
        return
    try:
        visit_id = int(args[0])
        region = args[1]
        doctor_id = int(args[2])
    except ValueError:
        print("visit_id and doctor_id must be integers.")
        return
    update_move(visit_id, region, doctor_id)
