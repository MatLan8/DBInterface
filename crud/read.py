"""Global READ (SELECT) — reconstruct a Visit from M1 + M2 fragments."""

import psycopg2.extras
from cassandra.query import SimpleStatement

import db  # use db.pg_conn / db.cassandra_sessions (not from-import — they change after connect)


NAME = "read"
USAGE = "read <visit_id>"
HELP = "Globally read a Visit (joins M1 Postgres + M2 Cassandra)"


def _fetch_postgres_visit(visit_id):
    """Look up visit in db11 then db12. Returns (fragment_info, row_dict) or (None, None)."""
    with db.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        for fragment in db.PG_FRAGMENTS:
            schema = fragment["schema"]
            cur.execute(
                f"""
                SELECT visit_id, region, patient_id, payment_status,
                       insurance_covered, total_amount_eur
                FROM {schema}.visit
                WHERE visit_id = %s
                """,
                (visit_id,),
            )
            row = cur.fetchone()
            if row is not None:
                return fragment, dict(row)
    return None, None


def _fetch_cassandra_visit(keyspace, visit_id):
    session = db.cassandra_sessions[keyspace]
    statement = SimpleStatement(
        "SELECT visit_id, region, doctor_id, visit_date, visit_location, "
        "symptoms, diagnosis, procedures, prescriptions, lab_results "
        "FROM visit WHERE visit_id = %s"
    )
    row = session.execute(statement, (visit_id,)).one()
    if row is None:
        return None
    return {
        "visit_id": row.visit_id,
        "region": row.region,
        "doctor_id": row.doctor_id,
        "visit_date": row.visit_date,
        "visit_location": row.visit_location,
        "symptoms": row.symptoms,
        "diagnosis": row.diagnosis,
        "procedures": list(row.procedures) if row.procedures else [],
        "prescriptions": list(row.prescriptions) if row.prescriptions else [],
        "lab_results": list(row.lab_results) if row.lab_results else [],
    }


def read_visit(visit_id):
    """
    Global SELECT for Visit:
      1) find financial row in Postgres horizontal fragments (db11, then db12)
      2) load medical row from matching Cassandra fragment (db21 / db22)
      3) return one joined logical object
    """
    fragment, financial = _fetch_postgres_visit(visit_id)
    if fragment is None:
        return {
            "found": False,
            "visit_id": visit_id,
            "message": f"Visit {visit_id} was not found in db11 or db12.",
        }

    cass_keyspace = fragment["cassandra"]
    medical = _fetch_cassandra_visit(cass_keyspace, visit_id)

    result = {
        "found": True,
        "visit_id": visit_id,
        "routing": {
            "region": fragment["region"],
            "postgres_schema": fragment["schema"],
            "cassandra_keyspace": cass_keyspace,
        },
        "financial": financial,
        "medical": medical,
        "visit": None,
        "warnings": [],
    }

    if medical is None:
        result["warnings"].append(
            f"Visit {visit_id} exists in {fragment['schema']} but is missing "
            f"from Cassandra keyspace {cass_keyspace} (integrity gap)."
        )
        result["visit"] = {
            "visit_id": financial["visit_id"],
            "region": financial["region"],
            **{k: v for k, v in financial.items() if k not in ("visit_id", "region")},
        }
    else:
        result["visit"] = {
            "visit_id": financial["visit_id"],
            "region": financial["region"],
            "patient_id": financial["patient_id"],
            "payment_status": financial["payment_status"],
            "insurance_covered": financial["insurance_covered"],
            "total_amount_eur": financial["total_amount_eur"],
            "doctor_id": medical["doctor_id"],
            "visit_date": medical["visit_date"],
            "visit_location": medical["visit_location"],
            "symptoms": medical["symptoms"],
            "diagnosis": medical["diagnosis"],
            "procedures": medical["procedures"],
            "prescriptions": medical["prescriptions"],
            "lab_results": medical["lab_results"],
        }

    return result


def run(args):
    """Interactive entry: args are tokens after the command name."""
    if len(args) != 1:
        print(f"Usage: {USAGE}")
        return
    try:
        visit_id = int(args[0])
    except ValueError:
        print("visit_id must be an integer.")
        return

    print(f"\n=== Global READ visit_id={visit_id} ===")
    db.print_json(read_visit(visit_id))
