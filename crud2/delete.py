"""DELETE by patient_id: remove patient + all their visits (+ claim/payment) on M1+M2.

Keeps doctor rows (independent).
"""

import time

import db

NAME = "delete_pat"
USAGE = "delete_pat <patient_id>"
HELP = "Delete patient and all related visits/claims/payments (keep doctors)"


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


def delete_pat(patient_id):
    t0 = time.perf_counter()
    schema = ks = region = None

    with db.pg_conn.cursor() as cur:
        for frag in db.PG_FRAGMENTS:
            cur.execute(
                f"SELECT patient_id FROM {frag['schema']}.patient_profile "
                f"WHERE patient_id = %s",
                (patient_id,),
            )
            if cur.fetchone():
                schema, ks, region = frag["schema"], frag["cassandra"], frag["region"]
                break

    if not schema:
        print(f"Error: patient_id={patient_id} not found in db11 or db12.")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    session = db.cassandra_sessions[ks]
    rows = 0
    visit_ids = []
    try:
        with db.pg_conn.cursor() as cur:
            cur.execute(
                f"SELECT visit_id FROM {schema}.visit WHERE patient_id = %s "
                f"ORDER BY visit_id",
                (patient_id,),
            )
            visit_ids = [r[0] for r in cur.fetchall()]

            for visit_id in visit_ids:
                rows += _delete_visit_tree(cur, schema, session, visit_id)

            cur.execute(
                f"DELETE FROM {schema}.patient_profile WHERE patient_id = %s",
                (patient_id,),
            )
            rows += cur.rowcount

        db.pg_conn.commit()
    except Exception as e:
        db.pg_conn.rollback()
        print(f"Error: delete failed — {e}")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    ms = (time.perf_counter() - t0) * 1000
    print(
        f"Deleted patient_id={patient_id} from {schema}+{ks} ({region}); "
        f"visits={visit_ids or 'none'}."
    )
    print(f"Rows deleted: {rows}")
    print(f"Time: {ms:.2f} ms")


def run(args):
    if len(args) != 1:
        print(f"Usage: {USAGE}")
        return
    try:
        delete_pat(int(args[0]))
    except ValueError:
        print("patient_id must be an integer.")
