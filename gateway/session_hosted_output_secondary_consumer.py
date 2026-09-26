"""A3 consumer of Output secondary retained publication.

The invitation to NEW-run hosted-room path calls this after a settled task.
It does not publish. It calls the Output owner's secondary contract. Send-consent
is not publication authority. A missing contract fails closed and writes nothing.
Primary artifact rows, ``_output_owner``, and ``runner.session_authority`` are
not touched here.
"""
from gateway.hosted_room_artifacts import RoomArtifactError


_CONTRACT = (
    'register_secondary_publication',
    'publish_secondary_publication',
    'retry_secondary_publication',
    'record_secondary_publication_failure',
    'complete_secondary_publication',
)


def _require_contract(service):
    for name in _CONTRACT:
        if not callable(getattr(service, name, None)):
            raise RoomArtifactError('Group Chat secondary publication is not registered')


def consume_secondary_retained_publication(
        service, task, *, route=None, publication_id=None, transport_error=None,
        confirm=False, consent=None):
    """Advance one secondary publication through the Output contract.

    ``confirm`` completes a published attempt the contract still accepts.
    ``transport_error`` is recorded against that attempt and is not stored in
    provenance. A blocked authorization failure stays blocked.
    """
    if consent is not None:
        refuse = getattr(service, 'publish_secondary_from_consent', None)
        if not callable(refuse):
            raise RoomArtifactError('Group Chat send consent is not publication authority')
        return refuse(task, consent)
    _require_contract(service)
    if publication_id is not None and not (isinstance(publication_id, str) and publication_id):
        raise RoomArtifactError('Group Chat secondary publication is not registered')

    current = None
    if publication_id is None or route is not None:
        if route is not None:
            current = service.register_secondary_publication(task, route=route)
        else:
            current = service.register_secondary_publication(task)
        if publication_id is not None and publication_id != current.get('publication_id'):
            raise RoomArtifactError('Group Chat secondary publication is not registered')
        publication_id = current['publication_id']
        if current.get('completed'):
            return current

    if current is None or (
            not current.get('published') and not current.get('blocked') and not current.get('completed')):
        current = service.publish_secondary_publication(task, publication_id)
    if current.get('completed'):
        return current
    if transport_error is not None:
        if current.get('published') or (current.get('blocked') and current.get('attempt')):
            return service.record_secondary_publication_failure(
                task, publication_id, attempt=current['attempt'], error=transport_error)
        return current
    if current.get('blocked') or not current.get('published') or not current.get('accepted'):
        current = service.retry_secondary_publication(task, publication_id)
    if current.get('completed'):
        return current
    if confirm:
        # Eligible attempts complete. A blocked, early, or stale attempt is
        # refused by the contract and does not gain a completion row.
        return service.complete_secondary_publication(
            task, publication_id, attempt=current['attempt'])
    return current
