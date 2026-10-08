"""One public-input normalizer shared by authority submission and private preparation."""
from hermes_state_runtime import RuntimeStoreError
from gateway.session_finite import admit_finite
from gateway.session_ingress_media import admit_attachments
from gateway.session_surface import admit_surface


def normalize_submission_payload(authority, actor, request):
    payload = {'text': request.payload['text'], **admit_finite(request.payload),
               **admit_surface(request.payload), **admit_attachments(request.payload.get('attachments'))}
    if 'classic_export_v1' in request.payload:
        from gateway.classic_output_exports import (
            CANONICAL_BINDING_VERSION,
            CANONICAL_MARKER_FIELDS,
        )
        marker = request.payload['classic_export_v1']
        if (not isinstance(marker, dict) or set(marker) != CANONICAL_MARKER_FIELDS
                or not isinstance(marker['export_id'], str) or not marker['export_id']
                or type(marker['generation']) is not int or marker['generation'] < 1
                or not isinstance(marker['group_id'], str) or not marker['group_id']
                or not isinstance(marker['principal_id'], str) or not marker['principal_id']
                or marker['principal_id'] != actor.subject
                or marker['binding_version'] != CANONICAL_BINDING_VERSION):
            raise RuntimeStoreError('invalid_params')
        payload['classic_export_v1'] = dict(marker)
    source = authority.sessions[request.ref.session_id].source
    if source is not None and source.user_id != actor.subject:
        # Durable server authorization, not a client payload field. The original
        # principal remains the admission/retry identity across owner restarts.
        payload['local_operator_v1'] = {'profile_id': authority.profile_id,
            'session_id': request.ref.session_id, 'principal_id': actor.subject}
    return payload
