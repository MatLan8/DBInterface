"""DELETE by doctor_id: remove doctor + all their visits (+ claim/payment) on M1+M2.

Keeps patient_profile (independent).
"""

import time

import db

NAME = "delete_doc"
USAGE = "delete_doc <doctor_id>"
HELP = "Delete doctor and all related visits/claims/payments (keep patients)"


def _delete_visit_tree(cur, schema, session, visit_id):
    """Delete payment + claim + PG visit + Cassandra visit. Returns rows removed."""
    rows = 0
    cur.execute(f"DELETE FROM {schema}.payment WHERE visit_id = %s", (visit_id,))
    rows += cur.rowcount
    cur.execute(
        f"DELETE FROM {schema}.insurance_claim WHERE visit_id = %s", (visit_id,)
    )
    rows += cur.rowcount
    cur.execute(f"DELETE FROM {schema}.visit WHERE visit_id = %s", (visit_id,))
    rows += cur.rowcount
    session.execute("DELETE FROM visit WHERE visit_id = %s", (visit_id,))
    rows += 1
    return rows


def delete_doc(doctor_id):
    t0 = time.perf_counter()
    found = []
    for ks in ("db21", "db22"):
        session = db.cassandra_sessions[ks]
        doc = session.execute(
            "SELECT doctor_id FROM doctor WHERE doctor_id = %s", (doctor_id,)
        ).one()
        if doc:
            found.append(ks)

    if not found:
        print(f"Error: doctor_id={doctor_id} not found in db21 or db22.")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    rows = 0
    visit_ids = []
    try:
        with db.pg_conn.cursor() as cur:
            for ks in found:
                session = db.cassandra_sessions[ks]
                visits = session.execute(
                    "SELECT visit_id, region FROM visit "
                    "WHERE doctor_id = %s ALLOW FILTERING",
                    (doctor_id,),
                )
                for v in visits:
                    visit_ids.append(v.visit_id)
                    frag = db.resolve_fragment(v.region)
                    if not frag:
                        print(
                            f"Warning: unknown region {v.region!r} "
                            f"on visit {v.visit_id}"
                        )
                        session.execute(
                            "DELETE FROM visit WHERE visit_id = %s", (v.visit_id,)
                        )
                        rows += 1
                        continue
                    rows += _delete_visit_tree(
                        cur, frag["schema"], session, v.visit_id
                    )

                session.execute(
                    "DELETE FROM doctor WHERE doctor_id = %s", (doctor_id,)
                )
                rows += 1

        db.pg_conn.commit()
    except Exception as e:
        db.pg_conn.rollback()
        print(f"Error: delete failed — {e}")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    ms = (time.perf_counter() - t0) * 1000
    print(
        f"Deleted doctor_id={doctor_id} from {', '.join(found)}; "
        f"visits={visit_ids or 'none'}."
    )
    print(f"Rows deleted: {rows}")
    print(f"Time: {ms:.2f} ms")


def run(args):
    if len(args) != 1:
        print(f"Usage: {USAGE}")
        return
    try:
        delete_doc(int(args[0]))
    except ValueError:
        print("doctor_id must be an integer.")
