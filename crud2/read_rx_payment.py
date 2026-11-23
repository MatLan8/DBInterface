"""READ: patient visits with prescriptions, payments, and insurance claims."""

import time

import psycopg2.extras

import db
from .table_out import print_table

NAME = "read_rx"
USAGE = "read_rx <patient_id>"
HELP = "Patient visits with payment, insurance claim, and prescriptions"


def _med_names(prescriptions):
    if not prescriptions:
        return ""
    names = []
    for p in prescriptions:
        name = getattr(p, "medication_name", None)
        if name is None and hasattr(p, "__getitem__"):
            name = p[1]  # prescription_udt: id, medication_name, ...
        names.append(str(name) if name is not None else "")
    return ", ".join(n for n in names if n)


def read_rx_payment(patient_id):
    t0 = time.perf_counter()
    rows = []

    with db.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for frag in db.PG_FRAGMENTS:
            schema, ks = frag["schema"], frag["cassandra"]
            cur.execute(
                f"SELECT patient_id, full_name FROM {schema}.patient_profile WHERE patient_id = %s",
                (patient_id,),
            )
            patient = cur.fetchone()
            if not patient:
                continue

            cur.execute(
                f"SELECT visit_id FROM {schema}.visit WHERE patient_id = %s ORDER BY visit_id",
                (patient_id,),
            )
            visit_ids = [r["visit_id"] for r in cur.fetchall()]
            session = db.cassandra_sessions[ks]
            for visit_id in visit_ids:
                cur.execute(
                    f"SELECT amount_paid FROM {schema}.payment WHERE visit_id = %s",
                    (visit_id,),
                )
                amounts = ", ".join(str(r["amount_paid"]) for r in cur.fetchall()) or ""

                cur.execute(
                    f"SELECT insurance_company, claim_amount FROM {schema}.insurance_claim WHERE visit_id = %s",
                    (visit_id,),
                )
                claims = cur.fetchall()
                companies = ", ".join(r["insurance_company"] for r in claims) if claims else ""
                claim_amounts = ", ".join(str(r["claim_amount"]) for r in claims) if claims else ""

                v = session.execute(
                    "SELECT diagnosis, symptoms, prescriptions FROM visit WHERE visit_id = %s",
                    (visit_id,),
                ).one()
                diagnosis = v.diagnosis if v else ""
                symptoms = v.symptoms if v else ""
                meds = _med_names(v.prescriptions) if v else ""

                rows.append(
                    (
                        patient["patient_id"],
                        patient["full_name"],
                        amounts,
                        companies,
                        claim_amounts,
                        diagnosis,
                        symptoms,
                        meds,
                    )
                )
            break

    ms = (time.perf_counter() - t0) * 1000
    if not rows:
        print(f"No visits found for patient_id={patient_id}.")
    else:
        print_table(
            (
                "patient_id",
                "patient_name",
                "amount_paid",
                "insurance_company",
                "claim_amount",
                "diagnosis",
                "symptoms",
                "medication",
            ),
            rows,
        )
    print(f"Time: {ms:.2f} ms")


def run(args):
    if len(args) != 1:
        print(f"Usage: {USAGE}")
        return
    try:
        read_rx_payment(int(args[0]))
    except ValueError:
        print("patient_id must be an integer.")
