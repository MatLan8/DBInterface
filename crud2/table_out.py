"""Shared CLI table formatting for CRUD reads."""


def print_table(headers, rows):
    """Print header + rows with | separators and even column spacing."""
    data = [["" if c is None else str(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in data:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(vals):
        return " | ".join(str(v).ljust(widths[i]) for i, v in enumerate(vals))

    print(fmt(headers))
    print("-+-".join("-" * w for w in widths))
    for row in data:
        print(fmt(row))
