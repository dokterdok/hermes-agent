"""Canonical Group Chat succession controls: ``groups.succession.status``, ``.prepare``, ``.promote``,
``.keep`` and ``.branch_log``.

``prepare`` and ``promote`` run on the computer the group should continue on (the target's own
gateway); ``keep`` runs on either of the two computers after ``continued_on_two``. Each checks
inside the call that the caller acts for the room's owner here: the subject recorded as its
owner on this computer, or this computer's operator (``not_owner`` otherwise). See
``gateway/hosted_room_succession_move.py``. Errors carry their stable code and, for clients,
only ``other`` or ``target``.
"""
from pathlib import Path

from hermes_state_runtime import RuntimeStoreError

TARGET_METHODS = frozenset({'groups.succession.status', 'groups.succession.prepare', 'groups.succession.promote',
                            'groups.succession.keep', 'groups.succession.branch_log'})
_CLIENT_DETAIL = ('other', 'target')


def context(authority, actor):
    """The move context for a caller of this computer's default profile."""
    from gateway import hosted_room_succession_move as move
    from gateway.platforms.api_server_room_succession import continuation_minter
    from gateway.session_authorities import served_profile_name
    from gateway.session_group_peers import _api_server
    if served_profile_name(Path(authority.profile_id)) != 'default':
        raise RuntimeStoreError('default_profile_required')
    adapter = _api_server(authority)
    store = getattr(adapter, '_run_idempotency_store', None)
    if store is None or store.durable is not True:
        raise RuntimeStoreError('durable_run_storage_required')
    db_path = Path(authority.db.db_path)
    return move.MoveContext(
        db_path=db_path, runs_store=store, service=getattr(authority, 'hosted_room_service', None),
        actor_subject=getattr(actor, 'subject', None),
        operator='session:operator' in getattr(actor, 'capabilities', ()),
        mint_grants=continuation_minter(adapter, db_path, replace_same_epoch=False))


def _text(params, key):
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeStoreError('invalid_params')
    return value


def dispatch_target(authority, actor, method, params):
    from gateway import hosted_room_succession_move as move
    from gateway import hosted_room_succession_return as returning
    from gateway.hosted_room_succession import SuccessionError
    from gateway.hosted_room_succession_status import status
    room_id = _text(params, 'room_id')
    ctx = context(authority, actor)
    try:
        if method == 'groups.succession.status':
            return status(ctx, room_id)
        if method == 'groups.succession.prepare':
            return move.preview(ctx, room_id, _text(params, 'target_install_id'))
        if method == 'groups.succession.promote':
            if set(params) != {'room_id', 'target_install_id', 'preview_id', 'confirm'}:
                raise RuntimeStoreError('invalid_params')
            return move.continue_here(ctx, room_id, _text(params, 'target_install_id'),
                                      preview_id=params['preview_id'], confirm=params['confirm'])
        if method == 'groups.succession.keep':
            return move.keep(ctx, room_id, _text(params, 'install_id'))
        after_seq, limit = params.get('after_seq', 0), params.get('limit', 200)
        if type(after_seq) is not int or after_seq < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise RuntimeStoreError('invalid_params')
        status(ctx, room_id)  # the caller may read this computer's record of the room at all
        return returning.branch_log(ctx.db_path, room_id, _text(params, 'branch_id'), after_seq=after_seq,
                                    limit=limit)
    except SuccessionError as exc:
        detail = {key: exc.detail[key] for key in _CLIENT_DETAIL if key in exc.detail}
        raise RuntimeStoreError(exc.reason, detail) from exc


def record_consent_owner(authority, actor, method, params):
    """The caller that consented here to continue a group becomes its owner on this computer."""
    from gateway.hosted_room_succession import record_owner_subject
    if params.get('successor') is not True or not isinstance(params.get('room_id'), str):
        return
    if method in {'groups.peer.invite', 'groups.custody.allow'}:
        record_owner_subject(authority.db.db_path, params['room_id'], getattr(actor, 'subject', None))
