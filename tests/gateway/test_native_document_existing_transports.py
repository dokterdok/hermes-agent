"""Strict opt-in preserves existing Signal/Cloud document transport contracts."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.native_document_guard import require_native_document


@pytest.mark.asyncio
@pytest.mark.parametrize('fail', [False, True])
async def test_signal_document_keeps_native_attachment_and_routing(tmp_path, monkeypatch, fail):
    from tests.gateway.test_signal import _make_signal_adapter

    adapter = _make_signal_adapter(monkeypatch)
    adapter.send = AsyncMock(side_effect=AssertionError('text is not a document'))
    adapter._stop_typing_indicator = AsyncMock()
    path = tmp_path / 'report.txt'
    path.write_bytes(b'exact document bytes\x00\xff')
    uploaded = []

    async def rpc(method, params, **kwargs):
        assert method == 'send'
        assert params['groupId'] == 'fixture-group'
        assert params['account'] == adapter.account
        assert params['message'] == '**Report** & <details>'
        uploaded.append(Path(params['attachments'][0]).read_bytes())
        return None if fail else {'timestamp': 123456}

    adapter._rpc = AsyncMock(side_effect=rpc)
    with require_native_document():
        result = await adapter.send_document('group:fixture-group', str(path),
            caption='**Report** & <details>', file_name='report.txt', reply_to='42',
            metadata={'group_file_delivery_id': 'fixture'})
    assert result.success is (not fail)
    assert uploaded == [path.read_bytes()]
    adapter.send.assert_not_awaited()
    # Signal's existing native attachment API does not report a message id or
    # use file_name/reply_to; strict opt-in does not invent either capability.
    if not fail:
        assert result.message_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [None, 'upload', 'message'])
async def test_cloud_document_keeps_upload_filename_caption_and_reply(tmp_path, failure):
    from tests.gateway.test_whatsapp_cloud import _make_adapter, _mock_upload_response, _mock_message_response

    adapter = _make_adapter()
    adapter.send = AsyncMock(side_effect=AssertionError('text is not a document'))
    adapter._http_client = MagicMock()
    path = tmp_path / 'source.pdf'
    path.write_bytes(b'%PDF-1.4 exact bytes')
    calls = []
    rejected = MagicMock(status_code=400)
    rejected.json.return_value = {'error': {'code': 100, 'message': 'controlled refusal'}}
    rejected.text = '{"error":{"code":100,"message":"controlled refusal"}}'

    async def post(url, **kwargs):
        if url.endswith('/media'):
            filename, handle, mime = kwargs['files']['file']
            calls.append((filename, handle.read(), mime))
            return rejected if failure == 'upload' else _mock_upload_response('media-document')
        assert url.endswith('/messages')
        payload = kwargs['json']
        assert payload['type'] == 'document'
        assert payload['document'] == {'id': 'media-document', 'filename': 'shared.pdf',
                                        'caption': '**Report** & <details>'}
        assert payload['context'] == {'message_id': 'parent-message'}
        assert payload['to'] == '15551234567'
        return rejected if failure == 'message' else _mock_message_response('native-document')

    adapter._http_client.post = AsyncMock(side_effect=post)
    with require_native_document():
        result = await adapter.send_document('15551234567', str(path),
            caption='**Report** & <details>', file_name='shared.pdf', reply_to='parent-message',
            metadata={'group_file_delivery_id': 'fixture'})
    assert result.success is (failure is None)
    if failure is None:
        assert result.message_id == 'native-document'
    assert calls == [('source.pdf', path.read_bytes(), 'application/pdf')]
    assert adapter._http_client.post.await_count == (1 if failure == 'upload' else 2)
    adapter.send.assert_not_awaited()


def test_marker_preserves_actual_method_and_context_restores_nested_guard():
    from gateway.native_document_guard import (
        NativeDocumentFallback, check_document_fallback, mark_native_document_guard,
    )

    async def native():
        return 'native'

    original = native
    assert mark_native_document_guard(native) is original
    assert native.strict_native_document_guard is True
    check_document_fallback()
    with require_native_document():
        with require_native_document():
            with pytest.raises(NativeDocumentFallback):
                check_document_fallback()
        with pytest.raises(NativeDocumentFallback):
            check_document_fallback()
    check_document_fallback()
