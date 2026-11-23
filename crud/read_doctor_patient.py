"""READ: patient visits with doctor (PG patient + Cass visit/doctor)."""

import time

import psycopg2.extras

import db
from .table_out import print_table

NAME = "read_dp"
USAGE = "read_dp <patient_id>"
HELP = "Patient visits with doctor (patient | diagnosis | symptoms | doctor)"


def read_doctor_patient(patient_id):
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
                v = session.execute(
                    "SELECT diagnosis, symptoms, doctor_id FROM visit WHERE visit_id = %s",
                    (visit_id,),
                ).one()
                doctor_name = ""
                diagnosis = symptoms = ""
                if v:
                    diagnosis, symptoms = v.diagnosis, v.symptoms
                    d = session.execute(
                        "SELECT full_name FROM doctor WHERE doctor_id = %s",
                        (v.doctor_id,),
                    ).one()
                    doctor_name = d.full_name if d else ""
                rows.append(
                    (patient["patient_id"], patient["full_name"], diagnosis, symptoms, doctor_name)
                )
            break

    ms = (time.perf_counter() - t0) * 1000
    if not rows:
        print(f"No visits found for patient_id={patient_id}.")
    else:
        print_table(
            ("patient_id", "patient_name", "diagnosis", "symptoms", "doctor_name"),
            rows,
        )
    print(f"Time: {ms:.2f} ms")


def run(args):
    if len(args) != 1:
        print(f"Usage: {USAGE}")
        return
    try:
        read_doctor_patient(int(args[0]))
    except ValueError:
        print("patient_id must be an integer.")
