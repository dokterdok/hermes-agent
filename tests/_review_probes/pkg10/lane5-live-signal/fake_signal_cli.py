"""Fake signal-cli daemon in HTTP mode (``signal-cli daemon --http 127.0.0.1:<port>``) for live retests.

Only the Signal network is replaced: the gateway runs the real ``plugins/platforms/signal/adapter.py``
against this process exactly as it would against signal-cli. It implements the part of signal-cli's
HTTP API the adapter uses:

  GET  /api/v1/check                  -> 200, empty body (the adapter's connect + health probe)
  GET  /api/v1/events?account=<E164>  -> text/event-stream, chunked; one ``event:receive`` +
                                         ``data:{"envelope":{...},"account":"<E164>"}`` per message,
                                         ``:`` keepalive comments every 15 s
  POST /api/v1/rpc                    -> JSON-RPC 2.0: getAttachment ({"data": base64}), send,
                                         sendTyping, sendReaction, sendReceipt (signal-cli's
                                         {"timestamp", "results":[{"recipientAddress", "type"}]}),
                                         listContacts, getContact; anything else -> {} (recorded)

plus a control surface that is NOT part of signal-cli (it stands in for "the user's phone"):

  POST /control/inject  {"envelopes": [...]}  -> writes them to every live SSE stream, in order,
                                                 back to back (one flush each, no delay)
  GET  /control/state                          -> SSE connect/live counts, RPC count
  POST /control/stop                           -> shuts the server down

Every request (and every SSE connect/disconnect/injection) is appended to --record as JSONL with a
wall-clock timestamp. Binds 127.0.0.1 only. Stdlib only, so it runs under any interpreter (-I).
"""
import argparse
import base64
import json
import queue
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PEER_UUID = 'aaaaaaaa-0000-4000-8000-000000000001'
PEER_NUMBER = '+15550001'


class State:
    def __init__(self, account, record, attachments):
        self.account = account
        self.attachments = attachments  # signal-cli attachment id -> local file (its attachments dir)
        self.lock = threading.Lock()
        self.subscribers = []  # one queue.Queue per live SSE stream
        self.connects = 0
        self.rpc_count = 0
        self.ts = int(time.time() * 1000)
        self.record = open(record, 'a', encoding='utf-8')

    def log(self, **entry):
        entry = {'t': time.time(), **entry}
        with self.lock:
            self.record.write(json.dumps(entry, default=str) + '\n')
            self.record.flush()

    def next_ts(self):
        with self.lock:
            self.ts = max(self.ts + 1, int(time.time() * 1000))
            return self.ts


def _ok_results(state, params):
    """signal-cli's SendMessageResults shape for send / sendTyping / sendReaction / sendReceipt."""
    recipients = params.get('recipient') or ([] if params.get('groupId') else [PEER_NUMBER])
    if isinstance(recipients, str):
        recipients = [recipients]
    return {'timestamp': state.next_ts(), 'results': [
        {'recipientAddress': {'uuid': PEER_UUID, 'number': PEER_NUMBER}, 'type': 'SUCCESS'} for _ in recipients]}


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'fake-signal-cli/0.1'

    def log_message(self, *args):
        pass

    @property
    def state(self):
        return self.server.state

    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return len(body)

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/api/v1/check':
            self.send_response(200)
            self.send_header('Content-Length', '0')
            self.end_headers()
            self.state.log(kind='http', method='GET', path=url.path, status=200)
            return
        if url.path == '/api/v1/events':
            return self._events(parse_qs(url.query).get('account', [''])[0])
        if url.path == '/control/state':
            with self.state.lock:
                live = len(self.state.subscribers)
            self._json(200, {'connects': self.state.connects, 'live': live, 'rpc_count': self.state.rpc_count})
            return
        self._json(404, {'error': 'not found'})

    def _chunk(self, data):
        self.wfile.write(b'%X\r\n' % len(data) + data + b'\r\n')
        self.wfile.flush()

    def _events(self, account):
        if account != self.state.account:
            self.state.log(kind='sse', event='rejected', account=account)
            self._json(400, {'error': f'account {account!r} is not registered here'})
            return
        q = queue.Queue()
        with self.state.lock:
            self.state.subscribers.append(q)
            self.state.connects += 1
            n = self.state.connects
        self.state.log(kind='sse', event='connect', n=n, peer=self.client_address)
        try:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            self.wfile.flush()
            while True:
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    self._chunk(b':\n\n')  # keepalive comment
                    continue
                if item is None:
                    break
                self._chunk(item)
                self.state.log(kind='sse', event='written', n=n, bytes=len(item))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError) as exc:
            self.state.log(kind='sse', event='disconnect', n=n, error=repr(exc))
        finally:
            with self.state.lock:
                if q in self.state.subscribers:
                    self.state.subscribers.remove(q)
            self.close_connection = True

    # ------------------------------------------------------------------ POST
    def do_POST(self):
        url = urlsplit(self.path)
        raw = self.rfile.read(int(self.headers.get('Content-Length') or 0))
        try:
            body = json.loads(raw or b'{}')
        except ValueError:
            body = {}
        if url.path == '/api/v1/rpc':
            return self._rpc(body)
        if url.path == '/control/inject':
            return self._inject(body)
        if url.path == '/control/stop':
            self._json(200, {'stopping': True})
            with self.state.lock:
                for q in self.state.subscribers:
                    q.put(None)
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self._json(404, {'error': 'not found'})

    def _inject(self, body):
        events = [('event:receive\ndata:' + json.dumps({'envelope': env, 'account': self.state.account})
                   + '\n\n').encode() for env in body.get('envelopes') or []]
        with self.state.lock:
            subscribers = list(self.state.subscribers)
            for q in subscribers:
                for event in events:
                    q.put(event)
        self.state.log(kind='inject', n_envelopes=len(events), subscribers=len(subscribers),
                       summary=[(e.get('timestamp'), (e.get('dataMessage') or {}).get('message'),
                                 [a.get('id') for a in (e.get('dataMessage') or {}).get('attachments') or []])
                                for e in body.get('envelopes') or []])
        self._json(200, {'delivered_to': len(subscribers), 'n': len(events)})

    def _rpc(self, req):
        started = time.time()
        method, params, rid = req.get('method'), req.get('params') or {}, req.get('id')
        with self.state.lock:
            self.state.rpc_count += 1
        entry = {'kind': 'rpc', 'method': method, 'id': rid, 'params': params}
        error = result = None
        if method == 'getAttachment':
            path = self.state.attachments.get(params.get('id'))
            if path is None:
                error = {'code': -1, 'message': f'Attachment not found: {params.get("id")}'}
            else:
                with open(path, 'rb') as fh:
                    result = {'data': base64.b64encode(fh.read()).decode('ascii')}
                entry['data_b64_chars'] = len(result['data'])
        elif method in ('send', 'sendTyping', 'sendReaction', 'sendReceipt'):
            result = _ok_results(self.state, params)
            entry['result_timestamp'] = result['timestamp']
        elif method == 'listContacts':
            result = [{'number': PEER_NUMBER, 'uuid': PEER_UUID, 'name': 'Test User',
                       'profile': {'givenName': 'Test', 'familyName': 'User'}}]
        elif method == 'getContact':
            result = {'number': PEER_NUMBER, 'uuid': PEER_UUID, 'name': 'Test User'}
        else:
            result = {}
            entry['unhandled'] = True
        reply = {'jsonrpc': '2.0', 'id': rid}
        reply['error' if error else 'result'] = error or result
        try:
            entry['response_bytes'] = self._json(200, reply)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError) as exc:
            entry['write_error'] = repr(exc)
        entry['duration_s'] = round(time.time() - started, 4)
        if error:
            entry['error'] = error
        self.state.log(**entry)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--account', required=True)
    ap.add_argument('--record', required=True)
    ap.add_argument('--port-file', required=True)
    ap.add_argument('--attachment', action='append', default=[], help='id=path')
    args = ap.parse_args()
    attachments = dict(item.split('=', 1) for item in args.attachment)
    srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    srv.daemon_threads = True
    srv.state = State(args.account, args.record, attachments)
    srv.state.log(kind='start', port=srv.server_port, attachments=attachments)
    with open(args.port_file + '.tmp', 'w') as fh:
        fh.write(str(srv.server_port))
    import os
    os.replace(args.port_file + '.tmp', args.port_file)
    try:
        srv.serve_forever(poll_interval=0.2)
    finally:
        srv.state.log(kind='stop')


if __name__ == '__main__':
    main()
