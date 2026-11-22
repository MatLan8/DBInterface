"""Global UPDATE — change a Visit across M1 + M2 (and move fragments if region changes).

TODO: implement dual-side update + region migration.
Use: import db  then db.pg_conn, db.cassandra_sessions, db.resolve_fragment, db.print_json
"""

NAME = "update"
USAGE = "update   (not implemented yet)"
HELP = "Globally update a Visit in M1 + M2 (TODO)"


def run(args):
    print("update: not implemented yet.")
    print(f"Usage: {USAGE}")
