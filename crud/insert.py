"""INSERT: doctor visit with prescription + lab result (M1 + M2)."""

import time
from collections import namedtuple
from datetime import date, datetime
from decimal import Decimal

import db

NAME = "insert_lab"
USAGE = (
    "insert_lab <region> <visit_id> <patient_id> <payment_status> <insurance_covered> "
    "<total_amount_eur> <claim_id> <insurance_company> <claim_amount> "
    "<payment_id> <amount_paid> <payment_method> <paid_at> "
    "<doctor_id> <visit_date> <visit_location> <symptoms> <diagnosis> "
    "<prescription_id> <medication_name> <dosage> <valid_until> "
    "<lab_result_id> <test_name> <result_value> <test_date>"
)
HELP = "Insert visit + claim + payment + prescription + lab (Vilnius/Kaunas)"

_Prescription = namedtuple(
    "prescription_udt", "prescription_id medication_name dosage valid_until"
)
_Lab = namedtuple(
    "lab_result_udt", "lab_result_id test_name result_value test_date"
)
_Procedure = namedtuple(
    "medical_procedure_udt", "procedure_id procedure_name category performed_at"
)

_ARG_COUNT = 26


def _parse_bool(v):
    s = str(v).strip().lower()
    if s in ("1", "true", "t", "yes", "y"):
        return True
    if s in ("0", "false", "f", "no", "n"):
        return False
    raise ValueError(f"boolean expected, got {v!r}")


def _parse_dt(v):
    s = str(v).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"datetime expected (YYYY-MM-DD[ HH:MM:SS]), got {v!r}")


def _parse_date(v):
    s = str(v).strip()
    try:
        return date.fromisoformat(s[:10])
    except ValueError as e:
        raise ValueError(f"date expected (YYYY-MM-DD), got {v!r}") from e


def _register_udts(session, keyspace):
    cluster = session.cluster
    cluster.register_user_type(keyspace, "prescription_udt", _Prescription)
    cluster.register_user_type(keyspace, "lab_result_udt", _Lab)
    cluster.register_user_type(keyspace, "medical_procedure_udt", _Procedure)


def insert_lab(
    region,
    visit_id,
    patient_id,
    payment_status,
    insurance_covered,
    total_amount_eur,
    claim_id,
    insurance_company,
    claim_amount,
    payment_id,
    amount_paid,
    payment_method,
    paid_at,
    doctor_id,
    visit_date,
    visit_location,
    symptoms,
    diagnosis,
    prescription_id,
    medication_name,
    dosage,
    valid_until,
    lab_result_id,
    test_name,
    result_value,
    test_date,
):
    frag = db.resolve_fragment(region)
    if not frag:
        print(f"Error: unknown region {region!r}. Use Vilnius or Kaunas.")
        return

    schema, ks = frag["schema"], frag["cassandra"]
    session = db.cassandra_sessions[ks]
    rows = 0
    t0 = time.perf_counter()

    with db.pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {schema}.patient_profile WHERE patient_id = %s",
            (patient_id,),
        )
        if not cur.fetchone():
            print(
                f"Error: patient_id={patient_id} not found in {schema} ({region})."
            )
            print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
            return

    doctor = session.execute(
        "SELECT doctor_id FROM doctor WHERE doctor_id = %s", (doctor_id,)
    ).one()
    if not doctor:
        print(f"Error: doctor_id={doctor_id} not found in {ks} ({region}).")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    _register_udts(session, ks)
    prescription = _Prescription(prescription_id, medication_name, dosage, valid_until)
    lab = _Lab(lab_result_id, test_name, result_value, test_date)

    try:
        with db.pg_conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO {schema}.visit
                    (visit_id, region, patient_id, payment_status,
                     insurance_covered, total_amount_eur)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    visit_id,
                    region,
                    patient_id,
                    payment_status,
                    insurance_covered,
                    total_amount_eur,
                ),
            )
            rows += cur.rowcount

            cur.execute(
                f"""
                INSERT INTO {schema}.insurance_claim
                    (claim_id, visit_id, insurance_company, claim_amount)
                VALUES (%s, %s, %s, %s)
                """,
                (claim_id, visit_id, insurance_company, claim_amount),
            )
            rows += cur.rowcount

            cur.execute(
                f"""
                INSERT INTO {schema}.payment
                    (payment_id, visit_id, amount_paid, payment_method, paid_at)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (payment_id, visit_id, amount_paid, payment_method, paid_at),
            )
            rows += cur.rowcount

        session.execute(
            """
            INSERT INTO visit
                (visit_id, region, doctor_id, visit_date, visit_location,
                 symptoms, diagnosis, prescriptions, lab_results, procedures)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                visit_id,
                region,
                doctor_id,
                visit_date,
                visit_location,
                symptoms,
                diagnosis,
                [prescription],
                [lab],
                [],
            ),
        )
        rows += 1  # cassandra visit
        rows += 1  # prescription udt
        rows += 1  # lab_result udt

        db.pg_conn.commit()
    except Exception as e:
        db.pg_conn.rollback()
        print(f"Error: insert failed — {e}")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    ms = (time.perf_counter() - t0) * 1000
    print(
        f"Inserted visit_id={visit_id} into {schema}+{ks} "
        f"(prescription + lab)."
    )
    print(f"Rows inserted: {rows}")
    print(f"Time: {ms:.2f} ms")


def run(args):
    if len(args) != _ARG_COUNT:
        print(f"Usage: {USAGE}")
        print(f"Expected {_ARG_COUNT} args, got {len(args)}.")
        return
    try:
        insert_lab(
            region=args[0],
            visit_id=int(args[1]),
            patient_id=int(args[2]),
            payment_status=args[3],
            insurance_covered=_parse_bool(args[4]),
            total_amount_eur=Decimal(args[5]),
            claim_id=int(args[6]),
            insurance_company=args[7],
            claim_amount=Decimal(args[8]),
            payment_id=int(args[9]),
            amount_paid=Decimal(args[10]),
            payment_method=args[11],
            paid_at=_parse_dt(args[12]),
            doctor_id=int(args[13]),
            visit_date=_parse_dt(args[14]),
            visit_location=args[15],
            symptoms=args[16],
            diagnosis=args[17],
            prescription_id=int(args[18]),
            medication_name=args[19],
            dosage=args[20],
            valid_until=_parse_date(args[21]),
            lab_result_id=int(args[22]),
            test_name=args[23],
            result_value=args[24],
            test_date=_parse_dt(args[25]),
        )
    except ValueError as e:
        print(f"Error: invalid argument — {e}")
