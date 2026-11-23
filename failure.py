"""Failure / recovery demo: PG visit insert, forced Cassandra failure, PG rollback."""

import db

NAME = "failure"
USAGE = "failure"
HELP = "Demo: insert visit in PG, fail Cassandra on purpose, rollback PG"


class CassandraUnavailable(RuntimeError):
    """Simulates Cassandra being unreachable during the dual-write."""


def simulate_failure(visit_id=99901, patient_id=1, region="Vilnius"):
    """Insert visit into Postgres, fail before Cassandra, then roll back PG."""
    print("\n--- FAILURE AND RECOVERY SCENARIO ---")
    print("1. Start coordinated CREATE (visit in M1 + M2).")
    print("2. Insert visit into PostgreSQL (not committed yet).")
    print("3. Intentionally fail Cassandra insert (DBMS unreachable).")
    print("4. Detect error and roll back the PostgreSQL visit.")

    frag = db.resolve_fragment(region)
    if not frag:
        print(f"Error: unknown region {region!r}.")
        return

    schema, ks = frag["schema"], frag["cassandra"]

    with db.pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT 1 FROM {schema}.patient_profile WHERE patient_id = %s",
            (patient_id,),
        )
        if not cur.fetchone():
            print(
                f"Error: patient_id={patient_id} not found in {schema} ({region}). "
                f"Cannot run failure demo."
            )
            return

        # Clean leftover from a previous interrupted demo
        cur.execute(f"DELETE FROM {schema}.visit WHERE visit_id = %s", (visit_id,))
    db.pg_conn.commit()

    session = db.cassandra_sessions[ks]
    session.execute("DELETE FROM visit WHERE visit_id = %s", (visit_id,))

    try:
        with db.pg_conn.cursor() as cur:
            print(f"\n-> Inserting visit_id={visit_id} into {schema}.visit ...")
            cur.execute(
                f"""
                INSERT INTO {schema}.visit
                    (visit_id, region, patient_id, payment_status,
                     insurance_covered, total_amount_eur)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (visit_id, region, patient_id, "pending", False, 0),
            )
            print(f"-> PostgreSQL row staged (uncommitted) in {schema}.")

            # Normal success path would be:
            #   session.execute("INSERT INTO visit ...")
            #   db.pg_conn.commit()
            print(f"-> Attempting Cassandra insert into {ks}.visit ...")
            raise CassandraUnavailable(
                f"Simulated failure: Cassandra keyspace '{ks}' unreachable"
            )
    except Exception as e:
        db.pg_conn.rollback()
        print(f"-> ERROR detected: {e}")
        print("-> Compensating: PostgreSQL transaction rolled back.")

    # Verify final state on both sides
    with db.pg_conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {schema}.visit WHERE visit_id = %s",
            (visit_id,),
        )
        pg_count = cur.fetchone()[0]

    cass_row = session.execute(
        "SELECT visit_id FROM visit WHERE visit_id = %s", (visit_id,)
    ).one()
    cass_count = 1 if cass_row else 0

    print(f"\nFinal PostgreSQL state ({schema}): {pg_count} visit row(s).")
    print(f"Final Cassandra state ({ks}): {cass_count} visit row(s).")

    if pg_count == 0 and cass_count == 0:
        print(
            "Recovery OK: no partial visit left in PostgreSQL or Cassandra."
        )
    else:
        print("Recovery check FAILED: partial data remains.")


def run(args):
    if args:
        print(f"Usage: {USAGE}")
        return
    simulate_failure()
