"""Admission-scoped finite consumers, independent of viewer or daemon lifetime."""
from contextlib import contextmanager
from contextvars import ContextVar

from hermes_state_runtime import RuntimeStoreError

_finite_turn = ContextVar('finite_turn', default=None)


def finite_turn_required():
    # None preserves legacy standalone CLI's marker. A bound owner turn must
    # never inherit process launch flags from another viewer.
    return _finite_turn.get()


@contextmanager
def finite_turn_scope(finite):
    token = _finite_turn.set(finite)
    try:
        yield
    finally:
        _finite_turn.reset(token)


def admit_finite(params):
    if 'finite' not in params:
        return {}
    if type(params['finite']) is not bool:
        raise RuntimeStoreError('invalid_params')
    return {'finite': params['finite']}


async def execute_finite_admission(authority, ref, row):
    import asyncio
    from gateway.session_ingress import execute_admission
    from gateway.session_hosted_output import hosted_output_scope, capture_output_result
    with finite_turn_scope(row['payload'].get('finite', False)):
        async with hosted_output_scope(authority, ref, row) as output:
            response = await execute_admission(authority, ref, row)
            await asyncio.to_thread(capture_output_result, authority, row, output)
            return response
