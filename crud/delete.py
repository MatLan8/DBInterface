"""Global DELETE — remove a Visit from M1 + M2 without leaving orphans.

TODO: delete Cassandra visit + Postgres visit and dependent payment/claim rows.
Use: import db  then db.pg_conn, db.cassandra_sessions, db.PG_FRAGMENTS, db.print_json
"""

NAME = "delete"
USAGE = "delete   (not implemented yet)"
HELP = "Globally delete a Visit from M1 + M2 (TODO)"


def run(args):
    print("delete: not implemented yet.")
    print(f"Usage: {USAGE}")
