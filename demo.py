"""Full lab demo: integrity, CRUD1/CRUD2, deletes, updates, failure recovery."""

import time
from datetime import date, datetime
from decimal import Decimal

import db
import failure
import integrity
from crud.delete import delete_doc
from crud.insert import insert_lab
from crud.read_doctor_patient import read_doctor_patient
from crud.update import update_move
from crud2.delete import delete_pat
from crud2.insert import insert_proc
from crud2.read_rx_payment import read_rx_payment
from crud2.update import update_rx

NAME = "demo"
USAGE = "demo"
HELP = "Run full demo sequence (integrity, CRUD, updates, failure)"

# Disposable high IDs — avoid colliding with seed data (1–20, …)
ID = {
    "doc_v": 98101,
    "doc_k": 98102,
    "pat_v": 98101,
    "pat_k": 98102,
    "visit_lab": 98101,
    "visit_proc": 98102,
    "del_doc": 98201,
    "del_pat_doc": 98202,
    "del_pat": 98202,
    "del_visit_doc": 98201,
    "del_visit_pat": 98202,
    "upd_doc_src": 98301,
    "upd_doc_dst": 98302,
    "upd_pat": 98301,
    "upd_visit_move": 98301,
    "upd_visit_rx": 98302,
    "upd_pat_rx": 98302,
}


def _banner(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _seed_doctor(doctor_id, keyspace, name):
    session = db.cassandra_sessions[keyspace]
    session.execute(
        """
        INSERT INTO doctor
            (doctor_id, full_name, license_number, specialty, workplace_clinic)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (doctor_id, name, f"LIC-{doctor_id}", "GP", f"Clinic-{keyspace}"),
    )


def _seed_patient(patient_id, region, full_name):
    frag = db.resolve_fragment(region)
    schema = frag["schema"]
    with db.pg_conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {schema}.patient_profile
                (patient_id, region, full_name, birth_date, phone, insurance_policy_no)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (patient_id) DO UPDATE SET
                region = EXCLUDED.region,
                full_name = EXCLUDED.full_name
            """,
            (
                patient_id,
                region,
                full_name,
                date(1990, 1, 1),
                f"+370{patient_id}",
                f"POL-{patient_id}",
            ),
        )
    db.pg_conn.commit()


def _patient_exists(patient_id, region):
    schema = db.resolve_fragment(region)["schema"]
    with db.pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT patient_id, full_name FROM {schema}.patient_profile "
            f"WHERE patient_id = %s",
            (patient_id,),
        )
        return cur.fetchone()


def _doctor_exists(doctor_id, keyspace):
    row = db.cassandra_sessions[keyspace].execute(
        "SELECT doctor_id, full_name FROM doctor WHERE doctor_id = %s",
        (doctor_id,),
    ).one()
    return row


def _cleanup_visit_tree(visit_id, region):
    frag = db.resolve_fragment(region)
    if not frag:
        return
    schema, ks = frag["schema"], frag["cassandra"]
    with db.pg_conn.cursor() as cur:
        cur.execute(f"DELETE FROM {schema}.payment WHERE visit_id = %s", (visit_id,))
        cur.execute(
            f"DELETE FROM {schema}.insurance_claim WHERE visit_id = %s", (visit_id,)
        )
        cur.execute(f"DELETE FROM {schema}.visit WHERE visit_id = %s", (visit_id,))
    db.pg_conn.commit()
    db.cassandra_sessions[ks].execute(
        "DELETE FROM visit WHERE visit_id = %s", (visit_id,)
    )


def _cleanup_patient(patient_id, region):
    schema = db.resolve_fragment(region)["schema"]
    with db.pg_conn.cursor() as cur:
        cur.execute(
            f"DELETE FROM {schema}.patient_profile WHERE patient_id = %s",
            (patient_id,),
        )
    db.pg_conn.commit()


def _cleanup_doctor(doctor_id, keyspace):
    db.cassandra_sessions[keyspace].execute(
        "DELETE FROM doctor WHERE doctor_id = %s", (doctor_id,)
    )


def _cleanup_all_demo_data():
    """Best-effort cleanup of disposable demo IDs in both fragments."""
    for vid in (
        ID["visit_lab"],
        ID["visit_proc"],
        ID["del_visit_doc"],
        ID["del_visit_pat"],
        ID["upd_visit_move"],
        ID["upd_visit_rx"],
    ):
        _cleanup_visit_tree(vid, "Vilnius")
        _cleanup_visit_tree(vid, "Kaunas")

    for pid, region in (
        (ID["pat_v"], "Vilnius"),
        (ID["pat_k"], "Kaunas"),
        (ID["del_doc"], "Vilnius"),
        (ID["del_pat"], "Vilnius"),
        (ID["upd_pat"], "Vilnius"),
        (ID["upd_pat"], "Kaunas"),
        (ID["upd_pat_rx"], "Vilnius"),
        (ID["upd_pat_rx"], "Kaunas"),
    ):
        _cleanup_patient(pid, region)

    for did, ks in (
        (ID["doc_v"], "db21"),
        (ID["doc_k"], "db22"),
        (ID["del_doc"], "db21"),
        (ID["del_pat_doc"], "db21"),
        (ID["upd_doc_src"], "db21"),
        (ID["upd_doc_dst"], "db22"),
    ):
        _cleanup_doctor(did, ks)


def run_demo():
    _banner("DEMO START — disposable IDs in 98xxx range")
    print("Cleaning any leftover demo rows...")
    _cleanup_all_demo_data()

    # ------------------------------------------------------------------
    # 1. Integrity
    # ------------------------------------------------------------------
    _banner("1. INTEGRITY CHECK")
    t0 = time.perf_counter()
    integrity.check_integrity()
    print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")

    # ------------------------------------------------------------------
    # Seed for main creates / reads (no timing)
    # ------------------------------------------------------------------
    _banner("SEED (no timing) — doctors/patients for create + read")
    _seed_doctor(ID["doc_v"], "db21", "Dr Demo Vilnius")
    _seed_doctor(ID["doc_k"], "db22", "Dr Demo Kaunas")
    _seed_patient(ID["pat_v"], "Vilnius", "Demo Patient Vilnius")
    _seed_patient(ID["pat_k"], "Kaunas", "Demo Patient Kaunas")
    print("Seeded doctors 98101/98102 and patients 98101/98102.")

    now = datetime(2026, 11, 23, 10, 0, 0)

    # ------------------------------------------------------------------
    # 2. Creates
    # ------------------------------------------------------------------
    _banner("2a. CREATE crud1 — insert_lab (Vilnius)")
    insert_lab(
        region="Vilnius",
        visit_id=ID["visit_lab"],
        patient_id=ID["pat_v"],
        payment_status="paid",
        insurance_covered=True,
        total_amount_eur=Decimal("100.00"),
        claim_id=ID["visit_lab"],
        insurance_company="Compensa",
        claim_amount=Decimal("40.00"),
        payment_id=ID["visit_lab"],
        amount_paid=Decimal("60.00"),
        payment_method="card",
        paid_at=now,
        doctor_id=ID["doc_v"],
        visit_date=now,
        visit_location="Demo Clinic V",
        symptoms="fever",
        diagnosis="flu",
        prescription_id=ID["visit_lab"],
        medication_name="Paracetamol",
        dosage="1 tablet",
        valid_until=date(2027, 1, 1),
        lab_result_id=ID["visit_lab"],
        test_name="CRP",
        result_value="5",
        test_date=now,
    )

    _banner("2b. CREATE crud2 — insert_proc (Kaunas)")
    insert_proc(
        region="Kaunas",
        visit_id=ID["visit_proc"],
        patient_id=ID["pat_k"],
        payment_status="paid",
        insurance_covered=False,
        total_amount_eur=Decimal("80.00"),
        claim_id=ID["visit_proc"],
        insurance_company="LD",
        claim_amount=Decimal("0.00"),
        payment_id=ID["visit_proc"],
        amount_paid=Decimal("80.00"),
        payment_method="cash",
        paid_at=now,
        doctor_id=ID["doc_k"],
        visit_date=now,
        visit_location="Demo Clinic K",
        symptoms="knee pain",
        diagnosis="sprain",
        procedure_id=ID["visit_proc"],
        procedure_name="Ultrasound",
        category="Diagnostic",
        performed_at=now,
    )

    # ------------------------------------------------------------------
    # 3. Reads
    # ------------------------------------------------------------------
    _banner("3a. READ crud1 — read_dp")
    read_doctor_patient(ID["pat_v"])

    _banner("3b. READ crud2 — read_rx")
    read_rx_payment(ID["pat_v"])

    # ------------------------------------------------------------------
    # 4–5. Deletes (seed + delete + show remaining)
    # ------------------------------------------------------------------
    _banner("SEED (no timing) — disposable data for deletes")
    _seed_doctor(ID["del_doc"], "db21", "Dr DeleteMe")
    _seed_patient(ID["del_doc"], "Vilnius", "Patient KeepAfterDocDelete")
    insert_lab(
        region="Vilnius",
        visit_id=ID["del_visit_doc"],
        patient_id=ID["del_doc"],
        payment_status="paid",
        insurance_covered=True,
        total_amount_eur=Decimal("50.00"),
        claim_id=ID["del_visit_doc"],
        insurance_company="Compensa",
        claim_amount=Decimal("20.00"),
        payment_id=ID["del_visit_doc"],
        amount_paid=Decimal("30.00"),
        payment_method="card",
        paid_at=now,
        doctor_id=ID["del_doc"],
        visit_date=now,
        visit_location="Del Clinic",
        symptoms="cough",
        diagnosis="cold",
        prescription_id=ID["del_visit_doc"],
        medication_name="Syrup",
        dosage="5ml",
        valid_until=date(2027, 1, 1),
        lab_result_id=ID["del_visit_doc"],
        test_name="CBC",
        result_value="ok",
        test_date=now,
    )

    _seed_doctor(ID["del_pat_doc"], "db21", "Dr KeepAfterPatDelete")
    _seed_patient(ID["del_pat"], "Vilnius", "Patient DeleteMe")
    insert_proc(
        region="Vilnius",
        visit_id=ID["del_visit_pat"],
        patient_id=ID["del_pat"],
        payment_status="paid",
        insurance_covered=False,
        total_amount_eur=Decimal("70.00"),
        claim_id=ID["del_visit_pat"],
        insurance_company="X",
        claim_amount=Decimal("0.00"),
        payment_id=ID["del_visit_pat"],
        amount_paid=Decimal("70.00"),
        payment_method="cash",
        paid_at=now,
        doctor_id=ID["del_pat_doc"],
        visit_date=now,
        visit_location="Del Clinic 2",
        symptoms="back pain",
        diagnosis="strain",
        procedure_id=ID["del_visit_pat"],
        procedure_name="Massage",
        category="Therapy",
        performed_at=now,
    )
    print("Seeded delete scenarios.")

    _banner("5a. DELETE crud1 — delete_doc (keep patient)")
    delete_doc(ID["del_doc"])
    pat = _patient_exists(ID["del_doc"], "Vilnius")
    doc = _doctor_exists(ID["del_doc"], "db21")
    print(f"Remaining patient_profile: {pat}")
    print(f"Remaining doctor (should be gone): {doc}")

    _banner("5b. DELETE crud2 — delete_pat (keep doctor)")
    delete_pat(ID["del_pat"])
    pat = _patient_exists(ID["del_pat"], "Vilnius")
    doc = _doctor_exists(ID["del_pat_doc"], "db21")
    print(f"Remaining patient_profile (should be gone): {pat}")
    print(f"Remaining doctor: {doc}")

    # ------------------------------------------------------------------
    # 6–7. Updates
    # ------------------------------------------------------------------
    _banner("SEED (no timing) — disposable data for updates")
    _seed_doctor(ID["upd_doc_src"], "db21", "Dr Move Source")
    _seed_doctor(ID["upd_doc_dst"], "db22", "Dr Move Target")
    _seed_patient(ID["upd_pat"], "Vilnius", "Patient Move")
    _seed_patient(ID["upd_pat_rx"], "Vilnius", "Patient RxUpdate")

    insert_lab(
        region="Vilnius",
        visit_id=ID["upd_visit_move"],
        patient_id=ID["upd_pat"],
        payment_status="paid",
        insurance_covered=True,
        total_amount_eur=Decimal("90.00"),
        claim_id=ID["upd_visit_move"],
        insurance_company="Compensa",
        claim_amount=Decimal("30.00"),
        payment_id=ID["upd_visit_move"],
        amount_paid=Decimal("60.00"),
        payment_method="card",
        paid_at=now,
        doctor_id=ID["upd_doc_src"],
        visit_date=now,
        visit_location="Move Clinic",
        symptoms="headache",
        diagnosis="migraine",
        prescription_id=ID["upd_visit_move"],
        medication_name="Ibuprofen",
        dosage="200mg",
        valid_until=date(2027, 1, 1),
        lab_result_id=ID["upd_visit_move"],
        test_name="ESR",
        result_value="10",
        test_date=now,
    )
    insert_lab(
        region="Vilnius",
        visit_id=ID["upd_visit_rx"],
        patient_id=ID["upd_pat_rx"],
        payment_status="paid",
        insurance_covered=True,
        total_amount_eur=Decimal("55.00"),
        claim_id=ID["upd_visit_rx"],
        insurance_company="Compensa",
        claim_amount=Decimal("15.00"),
        payment_id=ID["upd_visit_rx"],
        amount_paid=Decimal("40.00"),
        payment_method="card",
        paid_at=now,
        doctor_id=ID["upd_doc_src"],
        visit_date=now,
        visit_location="Rx Clinic",
        symptoms="eye irritation",
        diagnosis="conjunctivitis",
        prescription_id=ID["upd_visit_rx"],
        medication_name="Drops",
        dosage="1 drop",
        valid_until=date(2027, 1, 1),
        lab_result_id=ID["upd_visit_rx"],
        test_name="Culture",
        result_value="neg",
        test_date=now,
    )
    print("Seeded update scenarios.")

    _banner("7a. UPDATE crud1 — update_move Vilnius → Kaunas")
    update_move(ID["upd_visit_move"], "Kaunas", ID["upd_doc_dst"])

    _banner("7b. UPDATE crud2 — update_rx dosage + amounts")
    update_rx(
        ID["upd_visit_rx"],
        ID["upd_visit_rx"],
        "2 drops",
        Decimal("25.50"),
        Decimal("10.00"),
    )

    # ------------------------------------------------------------------
    # 8. Failure demo (no extra timing wrapper)
    # ------------------------------------------------------------------
    _banner("8. FAILURE / RECOVERY DEMO")
    failure.simulate_failure()

    # ------------------------------------------------------------------
    # Cleanup demo rows so re-runs stay clean
    # ------------------------------------------------------------------
    _banner("CLEANUP demo rows")
    _cleanup_all_demo_data()
    # patient left after delete_doc
    _cleanup_patient(ID["del_doc"], "Vilnius")
    _cleanup_doctor(ID["del_pat_doc"], "db21")
    print("Demo disposable data removed.")

    _banner("DEMO COMPLETE")


def run(args):
    if args:
        print(f"Usage: {USAGE}")
        return
    run_demo()
