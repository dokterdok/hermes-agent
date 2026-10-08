"""Real native invitation daemon with one explicit post-grant publication fault."""
import runpy
import sqlite3
from pathlib import Path

from gateway.platforms.api_server_run_idempotency import RunIdempotencyStore
from hermes_constants import get_hermes_home


class FaultConnection:
    def __init__(self, connection, home):
        self.connection, self.home = connection, home

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        marker = self.home / 'fail-native-publication'
        if marker.exists():
            marker.unlink()
            (self.home / 'native-publication-failed').touch()
            raise sqlite3.OperationalError('fixture native publication commit failure')
        return self.connection.commit()


publish = RunIdempotencyStore.commit_room_invitation


def one_publication_fault(self, claims, previous, previous_home, commit_reservation):
    home = Path(get_hermes_home())
    if claims['home_install_id'] != 'successor' or not (home / 'fail-native-publication').exists():
        return publish(self, claims, previous, previous_home, commit_reservation)
    original = self._conn
    self._conn = FaultConnection(original, home)
    try:
        return publish(self, claims, previous, previous_home, commit_reservation)
    finally:
        self._conn = original


RunIdempotencyStore.commit_room_invitation = one_publication_fault
runpy.run_path(str(Path(__file__).with_name('peer_cancellation_daemon.py')), run_name='__main__')
