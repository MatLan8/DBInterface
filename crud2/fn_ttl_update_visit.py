"""TTL: replace a visit's prescriptions with a value that expires."""

import time

import db

NAME = "ttl_visit"
USAGE = "ttl_visit <region> <visit_id> <ttl_seconds>"
HELP = "Set a TTL on a visit's prescriptions in Cassandra (Vilnius/Kaunas)"


def update_visit_prescriptions_with_ttl(
    session,
    visit_id,
    prescriptions,
    ttl_seconds,
):
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise ValueError("ttl_seconds must be a positive integer")
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be greater than zero")
    if prescriptions is None:
        raise ValueError("visit has no prescriptions value to expire")

    result = session.execute(
        """
        UPDATE visit USING TTL %s
        SET prescriptions = %s
        WHERE visit_id = %s
        IF EXISTS
        """,
        (ttl_seconds, prescriptions, visit_id),
    ).one()
    return bool(result and result.applied)


def run(args):
    if len(args) != 3:
        print(f"Usage: {USAGE}")
        return

    region, visit_id, ttl_seconds = args
    try:
        visit_id = int(visit_id)
        ttl_seconds = int(ttl_seconds)
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
    except ValueError as e:
        print(f"Error: invalid argument — {e}")
        return

    fragment = db.resolve_fragment(region)
    if not fragment:
        print(f"Error: unknown region {region!r}. Use Vilnius or Kaunas.")
        return

    t0 = time.perf_counter()
    session = db.cassandra_sessions[fragment["cassandra"]]
    try:
        row = session.execute(
            "SELECT prescriptions FROM visit WHERE visit_id = %s",
            (visit_id,),
        ).one()
        if row is None:
            print(
                f"Error: visit_id={visit_id} not found in "
                f"{fragment['cassandra']}."
            )
            print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
            return
        if row.prescriptions is None:
            print(f"Error: visit_id={visit_id} has no prescriptions value to expire.")
            print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
            return

        updated = update_visit_prescriptions_with_ttl(
            session,
            visit_id,
            row.prescriptions,
            ttl_seconds,
        )
    except Exception as e:
        print(f"Error: TTL update failed — {e}")
        print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
        return

    if updated:
        print(
            f"Set TTL={ttl_seconds}s on prescriptions for visit_id={visit_id} "
            f"in {fragment['cassandra']}."
        )
    else:
        print(
            f"Error: visit_id={visit_id} was deleted before the TTL update "
            f"in {fragment['cassandra']}."
        )
    print(f"Time: {(time.perf_counter() - t0) * 1000:.2f} ms")
