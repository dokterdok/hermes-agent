"""A retired preparation owner cannot overwrite its replacement or hide that refusal."""

import asyncio
import json
import logging
from types import SimpleNamespace

import pytest

from gateway import session_logical_preparation as preparation
from hermes_state import SessionDB
from hermes_state_logical_attempts import _WORKER_STATUS_KEY, record_logical_preparation_worker
from hermes_state_runtime import RuntimeStoreError, begin_runtime_epoch


@pytest.mark.asyncio
async def test_failure_record_refusal_is_visible_and_preserves_replacement_owner(tmp_path, monkeypatch, caplog):
    with SessionDB(tmp_path / "state.db") as db:
        epoch = begin_runtime_epoch(db, instance_id="old-owner")
        authority = SimpleNamespace(db=db, epoch=epoch, profile_id=str(tmp_path),
                                    runner=SimpleNamespace(config=SimpleNamespace(multiplex_profiles=False)))
        current_verdict = []

        def lose_owner(*args, **kwargs):
            current = begin_runtime_epoch(db, instance_id="replacement")
            record_logical_preparation_worker(db, epoch=current, state="running")
            current_verdict.append(db.get_meta(_WORKER_STATUS_KEY))
            raise RuntimeStoreError("storage_unavailable")

        monkeypatch.setattr(preparation, "prepare_logical_attempt_index", lose_owner)
        with caplog.at_level(logging.WARNING, logger="gateway.session_logical_preparation"):
            await preparation._drain(authority, asyncio.Event())
        assert authority._logical_preparation_state == "failed"
        assert db.get_meta(_WORKER_STATUS_KEY) == current_verdict[0]
        assert json.loads(current_verdict[0])["epoch"] > epoch
        assert any(record.levelno == logging.WARNING and "stale_epoch" in record.getMessage()
                   for record in caplog.records)
