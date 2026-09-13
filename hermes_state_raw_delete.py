"""Raw history maintenance cannot retire canonical runtime records."""
import logging

from hermes_state_common import _id_chunks, _placeholders


class SessionLedgerProtectedError(ValueError):
    reason = 'runtime_coordination_required'

    def __init__(self):
        super().__init__('Session history has retained runtime records. '
                         'Delete it through the owning gateway; nothing was deleted.')


LEDGER_REFERENCES_SQL = '''(
    {session_id} IN (SELECT target_session_id FROM session_admissions)
    OR {session_id} IN (SELECT session_id FROM worker_executions)
)'''


def protected_session_ids(conn, session_ids):
    """Same-transaction FK owners, irrespective of live or terminal status."""
    protected = set()
    for chunk in _id_chunks(set(session_ids)):
        protected.update(row[0] for row in conn.execute(
            f'SELECT s.id FROM sessions s WHERE s.id IN ({_placeholders(chunk)}) AND '
            + LEDGER_REFERENCES_SQL.format(session_id='s.id'), chunk))
    return protected


def require_unowned_delete(conn, session_ids):
    if protected_session_ids(conn, session_ids):
        raise SessionLedgerProtectedError()


def report_maintenance(report, *, skipped_protected, removed=None):
    """Only publish counts after the caller's transaction has succeeded."""
    if report is not None:
        report['skipped_protected'] = skipped_protected
        if removed is not None:
            report['removed'] = removed
    if removed is not None and skipped_protected:
        logging.getLogger('hermes_state').info(
            'Raw history cleanup removed %d sessions; skipped %d with retained runtime records',
            removed, skipped_protected)
