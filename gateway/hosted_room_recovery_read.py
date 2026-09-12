"""Read-only primitives from the accepted recovery fence, with no fence writer."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from gateway.hosted_rooms_common import identifier


def literal_id(value):
    checked = identifier(value, label='recovery identifier', error=ValueError)
    if type(value) is not str or checked != value:
        raise ValueError('Recovery identifiers must be literal strings')
    return checked


@contextmanager
def readonly(path):
    """Do not create, migrate, prune, hydrate or change the observed journal."""
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.close()
