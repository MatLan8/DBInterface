"""Global INSERT — create linked Visit data in M1 + M2 horizontal fragments.

TODO: implement with shared visit_id and region-based routing.
Use: import db  then db.pg_conn, db.cassandra_sessions, db.resolve_fragment, db.print_json
"""

NAME = "insert"
USAGE = "insert   (not implemented yet)"
HELP = "Globally insert a Visit into M1 + M2 (TODO)"


def run(args):
    print("insert: not implemented yet.")
    print(f"Usage: {USAGE}")
