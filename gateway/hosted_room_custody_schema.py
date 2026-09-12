"""Custody schema and durable installation marker; no global PRAGMA version."""
import re

from gateway.hosted_rooms_common import table_exists

TABLE = 'hosted_room_local_custody'
MARKER = 'hosted_room_custody_migrations'
MAX_ROWS = 512
MAX_BYTES = 32768


def definitions():
    statements = []
    statements.append(f"""CREATE TABLE {TABLE} (
        room_id TEXT NOT NULL, member_id TEXT NOT NULL, profile TEXT NOT NULL,
        gateway_id TEXT NOT NULL, session_id TEXT NOT NULL, session_started_at REAL NOT NULL,
        first_task_id TEXT NOT NULL, first_execution_generation INTEGER NOT NULL,
        created_at REAL NOT NULL, last_session_id TEXT NOT NULL, last_session_started_at REAL NOT NULL,
        chain_json TEXT NOT NULL CHECK(length(CAST(chain_json AS BLOB))<=32768),
        PRIMARY KEY(room_id,member_id), UNIQUE(room_id,profile), UNIQUE(profile,session_id))""")
    statements.append(f"""CREATE TRIGGER {TABLE}_bound BEFORE INSERT ON {TABLE}
        WHEN (SELECT COUNT(*) FROM {TABLE}) >= {MAX_ROWS}
        OR EXISTS(SELECT 1 FROM {TABLE} WHERE (room_id=NEW.room_id AND member_id=NEW.member_id)
            OR (room_id=NEW.room_id AND profile=NEW.profile) OR (profile=NEW.profile AND session_id=NEW.session_id))
        BEGIN SELECT RAISE(ABORT,'custody capacity or identity conflict'); END""")
    fields = ('room_id', 'member_id', 'profile', 'gateway_id', 'session_id', 'session_started_at',
              'first_task_id', 'first_execution_generation', 'created_at')
    changed = ' OR '.join(f'NEW.{k} IS NOT OLD.{k}' for k in fields)
    statements.append(f"""CREATE TRIGGER {TABLE}_immutable BEFORE UPDATE ON {TABLE}
        WHEN {changed} OR NEW.last_session_started_at < OLD.last_session_started_at
        BEGIN SELECT RAISE(ABORT,'immutable custody'); END""")
    statements.append(f"""CREATE TRIGGER {TABLE}_permanent BEFORE DELETE ON {TABLE}
        BEGIN SELECT RAISE(ABORT,'permanent custody'); END""")
    return statements


MARKER_DDL = f"CREATE TABLE {MARKER} (version INTEGER NOT NULL PRIMARY KEY CHECK(version=1))"
MARKER_TRIGGERS = [f"CREATE TRIGGER {MARKER}_{action.lower()} BEFORE {action} ON {MARKER} "
                   "BEGIN SELECT RAISE(ABORT,'permanent custody migration'); END"
                   for action in ('UPDATE', 'DELETE')]


def fail():
    from gateway.hosted_room_local_custody import fail as custody_fail
    return custody_fail()


def normalized(sql):
    return re.sub(r'\s+', '', (sql or '').lower().replace('if not exists', ''))


def validate_definitions(conn, statements):
    for ddl in statements:
        kind, name = ddl.split()[1:3]
        row = conn.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind.lower(), name)).fetchone()
        if row is None or normalized(row[0]) != normalized(ddl):
            raise fail()


def validate_schema(conn):
    validate_definitions(conn, definitions())
    fields = ('room_id', 'member_id', 'profile', 'gateway_id', 'session_id', 'session_started_at',
              'first_task_id', 'first_execution_generation', 'created_at', 'last_session_id',
              'last_session_started_at', 'chain_json')
    types = ('TEXT', 'TEXT', 'TEXT', 'TEXT', 'TEXT', 'REAL', 'TEXT', 'INTEGER', 'REAL', 'TEXT', 'REAL', 'TEXT')
    expected = [(name, kind, 1, None, (index + 1 if index < 2 else 0), 0)
                for index, (name, kind) in enumerate(zip(fields, types, strict=True))]
    actual = [tuple(row) for row in conn.execute(
        'SELECT name,type,[notnull],dflt_value,pk,hidden FROM pragma_table_xinfo(?) ORDER BY cid', (TABLE,))]
    if actual != expected:
        raise fail()
    if conn.execute('SELECT 1 FROM pragma_foreign_key_list(?)', (TABLE,)).fetchone():
        raise fail()
    unique = {tuple(row[0] for row in conn.execute('SELECT name FROM pragma_index_info(?) ORDER BY seqno', (index[0],)))
              for index in conn.execute('SELECT name FROM pragma_index_list(?) WHERE [unique]=1 AND partial=0', (TABLE,))}
    if unique != {('room_id', 'member_id'), ('room_id', 'profile'), ('profile', 'session_id')}:
        raise fail()
    # Exact DDL also covers types/not-null/PK/CHECK; no trigger may supplement
    # the supported protections with unexpected cascading side effects.
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name=?", (TABLE,))}
    if names != {TABLE + '_' + suffix for suffix in ('bound', 'immutable', 'permanent')}:
        raise fail()
    validate_definitions(conn, [MARKER_DDL, *MARKER_TRIGGERS])
    if [tuple(row) for row in conn.execute(f'SELECT version FROM {MARKER} LIMIT 2')] != [(1,)]:
        raise fail()
    validate_bounds(conn)


def validate_bounds(conn):
    count = conn.execute(f'SELECT COUNT(*) FROM (SELECT 1 FROM {TABLE} LIMIT ?)', (MAX_ROWS + 1,)).fetchone()[0]
    if count > MAX_ROWS:
        raise fail()
    columns = ('room_id', 'member_id', 'profile', 'gateway_id', 'session_id', 'first_task_id', 'last_session_id')
    predicate = ' OR '.join(f'length(CAST({field} AS BLOB))>128' for field in columns)
    if conn.execute(f'SELECT 1 FROM {TABLE} WHERE length(CAST(chain_json AS BLOB))>? OR {predicate} LIMIT 1',
                    (MAX_BYTES,)).fetchone():
        raise fail()


def initialize_schema(conn):
    exists = table_exists(conn, TABLE)
    marked = table_exists(conn, MARKER)
    if marked:
        # Losing a current table or protection is damage, not a migration.
        validate_schema(conn)
        return False
    if exists:
        # An existing custody table without the ledger is not a pre-feature
        # store. Do not infer authorization to reconstruct lost migration state.
        raise fail()
    for ddl in definitions():
        conn.execute(ddl)
    conn.execute(MARKER_DDL)
    conn.execute(f'INSERT INTO {MARKER} VALUES(1)')
    for ddl in MARKER_TRIGGERS:
        conn.execute(ddl)
    validate_schema(conn)
    return True
