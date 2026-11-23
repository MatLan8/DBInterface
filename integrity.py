"""Automatic integrity check across M1 (Postgres) + M2 (Cassandra) fragments."""

import db

NAME = "integrity"
USAGE = "integrity"
HELP = "Run integrity checks across M1 + M2 horizontal fragments"


def _fmt(value):
    return value if value else "none"


def check_integrity():
    print("\n--- AUTOMATIC INTEGRITY CHECK (M1 + M2) ---")

    pg_visits = {}  # schema -> {visit_id: region, patient_id}
    pg_patients = {}  # schema -> {patient_id: region}
    pg_payments = {}  # schema -> [(payment_id, visit_id)]
    pg_claims = {}  # schema -> [(claim_id, visit_id)]
    cass_visits = {}  # keyspace -> {visit_id: (region, doctor_id)}
    cass_doctors = {}  # keyspace -> set(doctor_id)

    with db.pg_conn.cursor() as cur:
        for frag in db.PG_FRAGMENTS:
            schema = frag["schema"]

            cur.execute(
                f"SELECT visit_id, region, patient_id FROM {schema}.visit"
            )
            pg_visits[schema] = {
                row[0]: {"region": row[1], "patient_id": row[2]} for row in cur.fetchall()
            }

            cur.execute(
                f"SELECT patient_id, region FROM {schema}.patient_profile"
            )
            pg_patients[schema] = {row[0]: row[1] for row in cur.fetchall()}

            cur.execute(
                f"SELECT payment_id, visit_id FROM {schema}.payment"
            )
            pg_payments[schema] = cur.fetchall()

            cur.execute(
                f"SELECT claim_id, visit_id FROM {schema}.insurance_claim"
            )
            pg_claims[schema] = cur.fetchall()

    for frag in db.PG_FRAGMENTS:
        ks = frag["cassandra"]
        session = db.cassandra_sessions[ks]
        visits = {}
        for row in session.execute(
            "SELECT visit_id, region, doctor_id FROM visit"
        ):
            visits[row.visit_id] = (row.region, row.doctor_id)
        cass_visits[ks] = visits
        cass_doctors[ks] = {
            row.doctor_id for row in session.execute("SELECT doctor_id FROM doctor")
        }

    # --- 1. Missing shared visit_ids (PG ↔ Cassandra), by fragment pair ---
    missing_in_cass = []
    missing_in_pg = []
    for frag in db.PG_FRAGMENTS:
        schema, ks, region = frag["schema"], frag["cassandra"], frag["region"]
        pg_ids = set(pg_visits[schema])
        cass_ids = set(cass_visits[ks])
        for vid in sorted(pg_ids - cass_ids):
            missing_in_cass.append(f"{vid} (in {schema}, missing in {ks})")
        for vid in sorted(cass_ids - pg_ids):
            missing_in_pg.append(f"{vid} (in {ks}, missing in {schema})")

    # --- 2. Duplicates across horizontal fragments ---
    dup_pg_visits = sorted(
        set(pg_visits["db11"]) & set(pg_visits["db12"])
    )
    dup_cass_visits = sorted(
        set(cass_visits["db21"]) & set(cass_visits["db22"])
    )
    dup_patients = sorted(
        set(pg_patients["db11"]) & set(pg_patients["db12"])
    )
    dup_doctors = sorted(
        cass_doctors["db21"] & cass_doctors["db22"]
    )

    # --- 3. Wrong horizontal fragment (region vs schema/keyspace) ---
    wrong_pg_visit_region = []
    wrong_pg_patient_region = []
    wrong_cass_visit_region = []
    for frag in db.PG_FRAGMENTS:
        schema, ks, expected = frag["schema"], frag["cassandra"], frag["region"]
        for vid, info in pg_visits[schema].items():
            if info["region"] != expected:
                wrong_pg_visit_region.append(
                    f"visit {vid} in {schema}: region={info['region']!r} "
                    f"(expected {expected!r})"
                )
        for pid, region in pg_patients[schema].items():
            if region != expected:
                wrong_pg_patient_region.append(
                    f"patient {pid} in {schema}: region={region!r} "
                    f"(expected {expected!r})"
                )
        for vid, (region, _doctor_id) in cass_visits[ks].items():
            if region != expected:
                wrong_cass_visit_region.append(
                    f"visit {vid} in {ks}: region={region!r} "
                    f"(expected {expected!r})"
                )

    # --- 4. Dependent rows must sit with their visit; orphans ---
    payment_wrong_or_orphan = []
    claim_wrong_or_orphan = []
    visit_missing_patient = []
    doctor_missing = []

    for frag in db.PG_FRAGMENTS:
        schema, ks = frag["schema"], frag["cassandra"]
        visit_ids = set(pg_visits[schema])
        patients = pg_patients[schema]

        for payment_id, visit_id in pg_payments[schema]:
            if visit_id not in visit_ids:
                payment_wrong_or_orphan.append(
                    f"payment {payment_id} in {schema}: "
                    f"visit_id={visit_id} has no visit in {schema}"
                )

        for claim_id, visit_id in pg_claims[schema]:
            if visit_id not in visit_ids:
                claim_wrong_or_orphan.append(
                    f"claim {claim_id} in {schema}: "
                    f"visit_id={visit_id} has no visit in {schema}"
                )

        for vid, info in pg_visits[schema].items():
            if info["patient_id"] not in patients:
                visit_missing_patient.append(
                    f"visit {vid} in {schema}: "
                    f"patient_id={info['patient_id']} missing in {schema}"
                )

        for vid, (_region, doctor_id) in cass_visits[ks].items():
            if doctor_id not in cass_doctors[ks]:
                doctor_missing.append(
                    f"visit {vid} in {ks}: doctor_id={doctor_id} "
                    f"missing in {ks}.doctor"
                )

    # --- 5. Region mismatch between PG visit and Cassandra visit (same id) ---
    region_mismatch = []
    for frag in db.PG_FRAGMENTS:
        schema, ks = frag["schema"], frag["cassandra"]
        for vid, info in pg_visits[schema].items():
            cass = cass_visits[ks].get(vid)
            if cass and cass[0] != info["region"]:
                region_mismatch.append(
                    f"visit {vid}: PG {schema} region={info['region']!r}, "
                    f"Cass {ks} region={cass[0]!r}"
                )

    errors = any(
        [
            missing_in_cass,
            missing_in_pg,
            dup_pg_visits,
            dup_cass_visits,
            dup_patients,
            wrong_pg_visit_region,
            wrong_pg_patient_region,
            wrong_cass_visit_region,
            payment_wrong_or_orphan,
            claim_wrong_or_orphan,
            visit_missing_patient,
            doctor_missing,
            region_mismatch,
        ]
    )

    print(f"Missing visit in Cassandra (PG has it): {_fmt(missing_in_cass)}")
    print(f"Missing visit in PostgreSQL (Cass has it): {_fmt(missing_in_pg)}")
    print(f"Duplicate visit_id in both PG schemas: {_fmt(dup_pg_visits)}")
    print(f"Duplicate visit_id in both Cass keyspaces: {_fmt(dup_cass_visits)}")
    print(f"Duplicate patient_id in both PG schemas: {_fmt(dup_patients)}")
    print(f"Doctor_id present in both Cass keyspaces (info): {_fmt(dup_doctors)}")
    print(f"Wrong region on PG visit: {_fmt(wrong_pg_visit_region)}")
    print(f"Wrong region on PG patient_profile: {_fmt(wrong_pg_patient_region)}")
    print(f"Wrong region on Cassandra visit: {_fmt(wrong_cass_visit_region)}")
    print(f"PG↔Cass visit region mismatch: {_fmt(region_mismatch)}")
    print(f"Orphan / wrong-fragment payments: {_fmt(payment_wrong_or_orphan)}")
    print(f"Orphan / wrong-fragment claims: {_fmt(claim_wrong_or_orphan)}")
    print(f"Visit without patient_profile: {_fmt(visit_missing_patient)}")
    print(f"Cassandra visit without doctor: {_fmt(doctor_missing)}")

    if errors:
        print("Integrity check found inconsistencies.")
    else:
        print("Integrity check passed.")

    return not errors


def run(args):
    if args:
        print(f"Usage: {USAGE}")
        return
    check_integrity()
