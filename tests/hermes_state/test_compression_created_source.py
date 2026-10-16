"""A compression child must not freeze the parent's live ``local`` label as created_source."""
from hermes_cli.gateway_session_store import SurfaceLabelReader
from hermes_state import SessionDB


def _compress(db, parent_created, child):
    db.create_session("parent-" + child, source="local")
    parent = "parent-" + child
    with db._lock:
        db._conn.execute("UPDATE sessions SET created_source=? WHERE id=?", (parent_created, parent))
        db._conn.commit()
    db.append_message(parent, role="user", content="hi")
    db.publish_compression_child(
        parent_session_id=parent, child_session_id=child, source="local",
        messages=[{"role": "user", "content": "hi"}], require_compression_lease=False)
    return parent


def test_local_parent_compression_child_heals_to_cli_and_a_real_surface_is_copied(tmp_path):
    """``source='local'`` is the live label, not a creating surface.

    A parent whose ``created_source`` is NULL or ``unknown`` must leave the child
    unset so the agent's ``create_session(source='cli')`` can stamp ``cli``. A
    parent that already has ``cli`` is copied as ``cli``.
    """
    db = SessionDB(db_path=tmp_path / "state.db")
    try:
        for child, parent_created in (("heal-null", None), ("heal-unknown", "unknown")):
            _compress(db, parent_created, child)
            assert db.get_session(child)["created_source"] is None
            db.create_session(child, source="cli")
            assert db.get_session(child)["created_source"] == "cli"
        _compress(db, "cli", "copied")
        assert db.get_session("copied")["created_source"] == "cli"
        found = [row["id"] for row in SurfaceLabelReader(db).search_sessions(source="cli", limit=10)]
        assert {"heal-null", "heal-unknown", "copied"} <= set(found)
    finally:
        db.close()
