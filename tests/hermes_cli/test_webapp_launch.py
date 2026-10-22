"""Operator handoff must not turn the launch credential into process-list or history data.

No URL a private launch opens or prints carries a credential (#93508 A1): a browser launcher and a
terminal click both hand the URL over in argv, which other local users can read. The session reaches
the browser only through the one-use code the operator types into the credential-free launch page.
"""
import io
import json
import os
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import subprocess
import sys
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pytest

from tests.waiting import wait_until


def _private_webapp(monkeypatch):
    from fastapi.testclient import TestClient
    from hermes_cli import web_server as server

    for name, value in (("ui_surface", "webapp"), ("auth_required", False), ("bound_host", "127.0.0.1"),
                        ("webapp_window_tickets", {}), ("webapp_launch_code", None)):
        monkeypatch.setattr(server.app.state, name, value, raising=False)
    return server, TestClient(server.app, base_url="http://127.0.0.1")


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    """A loopback Webapp's private launch, its scheduled browser opener run inline and recorded, and
    a buffer standing in for the operator's terminal."""
    import webbrowser
    from hermes_cli import web_server as server, web_server_lifecycle as lifecycle

    monkeypatch.setattr(server.app.state, "ui_surface", "webapp", raising=False)
    monkeypatch.setattr(server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(server.app.state, "webapp_launch_code", None, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":test")
    # Execute the scheduled opener deterministically, without launching a browser.
    monkeypatch.setattr(lifecycle.threading, "Thread", lambda target, **kw: SimpleNamespace(start=target))
    monkeypatch.setattr(lifecycle.time, "sleep", lambda seconds: None)
    opened, terminal = [], io.StringIO()
    monkeypatch.setattr(webbrowser, "open", opened.append)
    monkeypatch.setattr(lifecycle, "operator_terminal", lambda: _operator(terminal))
    return SimpleNamespace(server=server, lifecycle=lifecycle, opened=opened, terminal=terminal)


@contextmanager
def _operator(stream):
    yield stream


def test_private_launch_prints_no_credential_and_rotates_its_token(launcher, monkeypatch, capsys, caplog):
    server, lifecycle, opened, terminal = launcher.server, launcher.lifecycle, launcher.opened, launcher.terminal
    # The code is shown on the operator's terminal only, never in stdout a redirect, a pipe or a
    # service log keeps; with no terminal there is no code to see at all.
    monkeypatch.setattr(lifecycle, "operator_terminal", lambda: _operator(None))
    lifecycle._maybe_open_browser("::1", 9123, False, "coder")
    assert "one-use code is shown only on a terminal" in capsys.readouterr().out
    assert server.app.state.webapp_launch_code is None
    monkeypatch.setattr(lifecycle, "operator_terminal", lambda: _operator(terminal))
    lifecycle._maybe_open_browser("::1", 9123, False, "coder")
    printed = capsys.readouterr().out
    assert not opened and re.search(r"one-use code there: [A-Z2-9]{5}-[A-Z2-9]{5} ", terminal.getvalue())
    assert "one-use code there" not in printed
    (launch,) = re.findall(r"https?://\S+", printed)
    parsed = urlsplit(launch)
    assert (parsed.hostname, parsed.path) == ("::1", "/webapp/launch")
    assert parse_qs(parsed.fragment) == {"query": ["?profile=coder"]}
    assert server._SESSION_TOKEN not in printed + caplog.text

    # Real startup credential branch, stopping at the socket construction seam.
    # Both inherited values and prior starts must be replaced for local Webapp.
    monkeypatch.setenv("HERMES_DASHBOARD_SESSION_TOKEN", "inherited")
    monkeypatch.setattr(server, "_SESSION_TOKEN", "inherited")
    monkeypatch.setattr(server, "_configure_auth_gate", lambda *a: None)
    monkeypatch.setattr("hermes_cli.nous_auth_keepalive.start_nous_auth_keepalive", lambda: None)
    def stop_at_bind(*args, **kwargs):
        raise RuntimeError("test bind boundary")
    monkeypatch.setattr(server, "_build_uvicorn_server", stop_at_bind)
    with pytest.raises(RuntimeError, match="test bind boundary"):
        server.start_server(port=0, open_browser=False, ui_surface="webapp")
    first = server._SESSION_TOKEN
    with pytest.raises(RuntimeError, match="test bind boundary"):
        server.start_server(port=0, open_browser=False, ui_surface="webapp")
    assert first != "inherited" and server._SESSION_TOKEN != first
    assert os.environ["HERMES_DASHBOARD_SESSION_TOKEN"] == "inherited"

    server.app.state.auth_required = True
    lifecycle._maybe_open_browser("127.0.0.1", 9123, True, "coder")
    assert not capsys.readouterr().out
    assert urlsplit(opened[-1]).fragment == ""


@pytest.mark.platforms("linux", "macos", "windows")
def test_private_auto_open_hands_the_launcher_neither_a_credential_nor_a_home_path(launcher, tmp_path, capsys, caplog):
    """Every platform opens the page itself, macOS included: nothing in the URL needs protecting, so
    the launcher gets exactly the credential-free page printed for the operator."""
    launcher.lifecycle._maybe_open_browser("::1", 9123, True, "coder")
    printed = capsys.readouterr().out
    (launch,) = launcher.opened
    assert re.findall(r"https?://\S+", printed) == [launch]
    parsed = urlsplit(launch)
    assert (parsed.hostname, parsed.path) == ("::1", "/webapp/launch")
    assert parse_qs(parsed.fragment) == {"query": ["?profile=coder"]}
    assert launcher.server._SESSION_TOKEN not in printed + launch + caplog.text
    assert str(tmp_path) not in launch


def test_launch_code_redeems_once_and_guessing_burns_it(monkeypatch):
    """The typed code is the only launch credential: one use, attempt-limited, bound to this start,
    and separate from the child windows' handoff tickets."""
    from hermes_cli.web_routers import webapp

    server, client = _private_webapp(monkeypatch)
    monkeypatch.setattr(server, "_SESSION_TOKEN", "a" * 43)

    def redeem(code):
        return client.post("/webapp/window-session", headers={"Origin": "http://127.0.0.1", "X-Hermes-Launch-Code": code})

    assert client.post("/api/webapp/launch-code").status_code == 401
    issued = client.post("/api/webapp/launch-code", headers={server._SESSION_HEADER_NAME: "a" * 43})
    assert issued.headers["cache-control"] == "no-store"  # a same-user launcher's fresh code
    assert redeem(issued.json()["code"]).status_code == 200
    code = webapp.mint_launch_code(server.app.state)
    granted = redeem(code.lower().replace("-", " "))  # typed loosely
    assert granted.status_code == 200 and granted.json() == {"token": "a" * 43}
    assert granted.headers["cache-control"] == "no-store"
    assert redeem(code).status_code == 403  # one use
    window = client.post("/api/webapp/window-ticket", headers={server._SESSION_HEADER_NAME: "a" * 43}).json()["ticket"]
    code = webapp.mint_launch_code(server.app.state)
    for _ in range(webapp.LAUNCH_CODE_ATTEMPTS):
        denied = redeem("AAAAA-AAAAA")
        assert denied.status_code == 403 and "a" * 43 not in denied.text
    assert redeem(code).status_code == 403  # burnt by the guesses
    child = client.post("/webapp/window-session", headers={"Origin": "http://127.0.0.1", "X-Hermes-Window-Ticket": window})
    assert child.json() == {"token": "a" * 43}  # a child window's ticket is not the code's to spend
    code = webapp.mint_launch_code(server.app.state)
    monkeypatch.setattr(server, "_SESSION_TOKEN", "b" * 43)  # another start
    assert redeem(code).status_code == 403
    code = webapp.mint_launch_code(server.app.state)
    now = webapp.time.monotonic()
    monkeypatch.setattr(webapp.time, "monotonic", lambda: now + webapp.LAUNCH_CODE_TTL_SECONDS + 1)
    assert redeem(code).status_code == 403


_SERVER = """
import os, sys
sys.path.insert(0, os.getcwd())
from hermes_cli.web_server import start_server
start_server(host="127.0.0.1", port=0, open_browser=True, ui_surface="webapp")
"""


@pytest.mark.platforms("posix")
def test_auto_open_hands_the_browser_launcher_no_credential_and_the_printed_code_works_once(tmp_path):
    """Real server, real browser dispatch: the URL a launcher receives (the argv other local users
    may read) carries neither a credential nor a Hermes-home file a snap/Flatpak browser cannot open.
    Started in its own session (no terminal, like a service), it puts no code in its output; a
    same-user launcher gets one with the owner-only token file, and it enters the session once."""
    home, os_home = tmp_path / "hermes-home", tmp_path / "os-home"
    home.mkdir()
    os_home.mkdir()
    ready, recorded = tmp_path / "ready.json", tmp_path / "browser-argv.txt"
    browser = tmp_path / "fake-browser"
    browser.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{recorded}'\n", encoding="utf-8")
    browser.chmod(0o755)
    env = {key: value for key, value in os.environ.items()
           if key not in {"HERMES_DESKTOP", "HERMES_DESKTOP_CHILD_PID", "HERMES_PARENT_PID", "HERMES_SPAWN",
                          "HERMES_DASHBOARD_SESSION_TOKEN", "HERMES_DASHBOARD_PUBLIC_URL"}}
    env.update(HERMES_HOME=str(home), HOME=str(os_home), HERMES_RUNTIME_DIR=str(tmp_path / "runtime"),
               HERMES_GATEWAY_LOCK_DIR=str(tmp_path / "locks"), HERMES_DESKTOP_READY_FILE=str(ready),
               BROWSER=str(browser), DISPLAY=":launch-test")
    log_path = tmp_path / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen([sys.executable, "-u", "-c", _SERVER], cwd=Path(__file__).resolve().parents[2],
                                  env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
    try:
        wait_until(lambda: (recorded.exists() and recorded.stat().st_size) or server.poll() is not None,
                   timeout=60, what="the browser launcher")
        output = log_path.read_text(encoding="utf-8-sig", errors="replace")
        assert recorded.exists() and recorded.stat().st_size and ready.exists(), output
        port = json.loads(ready.read_text(encoding="utf-8-sig"))["port"]
        assert "one-use code there" not in output and "shown only on a terminal" in output, output
        owner_token = (tmp_path / "locks" / "host-serve.token").read_text(encoding="utf-8").strip()
        fresh = Request(f"http://127.0.0.1:{port}/api/webapp/launch-code", data=b"", method="POST",
                        headers={"Authorization": f"Bearer {owner_token}"})
        with urlopen(fresh, timeout=10) as issued:
            code = json.loads(issued.read())["code"]

        launched = recorded.read_text(encoding="utf-8-sig").split()[-1]
        target = urlsplit(launched)
        assert (target.scheme, target.hostname, target.port, target.path) == ("http", "127.0.0.1", port, "/webapp/launch")
        assert not parse_qs(target.fragment) and str(tmp_path) not in launched

        origin = f"http://127.0.0.1:{port}"
        with urlopen(f"{origin}{target.path}", timeout=10) as page:
            assert page.status == 200 and code not in page.read().decode()
        exchange = Request(f"{origin}/webapp/window-session", data=b"", method="POST", headers={
            "Origin": origin, "X-Hermes-Launch-Code": code})
        with urlopen(exchange, timeout=10) as granted:
            token = json.loads(granted.read())["token"]
        assert token not in launched and token not in output
        with pytest.raises(HTTPError) as replay:
            urlopen(exchange, timeout=10)
        assert replay.value.code == 403 and token not in replay.value.read().decode()
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


# The served launch page's script, run as a browser tab runs it, with the browser globals it touches
# stubbed: the operator types ``env.typed`` and submits; out records what the page kept in
# sessionStorage, the credential headers it sent, where it navigated and what it told the operator.
_PAGE_HARNESS = r"""
const env = JSON.parse(process.env.PAGE_ENV);
const out = {stored: {}, sent: [], replaced: null, status: ''};
const elements = {
  status: {set textContent(text) { out.status = text; }},
  code: {value: env.typed, focus() {}},
  launch: {addEventListener(type, handler) { out.submit = handler; }},
};
const stub = (name, value) => Object.defineProperty(globalThis, name, {value, configurable: true, writable: true});
stub('location', {hash: env.hash, pathname: '/webapp/launch', origin: 'http://127.0.0.1:9123',
                  replace: url => { out.replaced = url; }});
stub('history', {replaceState() {}});
stub('window', {name: ''});
stub('document', {getElementById: id => elements[id]});
stub('sessionStorage', {setItem(key, value) { if (!env.storage) throw new Error('blocked'); out.stored[key] = value; }});
stub('fetch', async (url, init) => { out.sent.push(init.headers); return {ok: env.ok, json: async () => env.reply}; });
process.once('beforeExit', () => { delete out.submit; out.windowName = window.name; console.log(JSON.stringify(out)); });
setImmediate(() => out.submit({preventDefault() {}}));
"""


def _run_launch_page(monkeypatch, *, typed, hash="", ok=True, storage=True):
    node = shutil.which("node")
    if node is None:
        pytest.skip("running the served launch page needs node")
    _, client = _private_webapp(monkeypatch)
    page = client.get("/webapp/launch").text
    script = re.search(r'<script nonce="[^"]+">(.*)</script>', page, re.DOTALL).group(1)
    env = {**os.environ, "PAGE_ENV": json.dumps(
        {"hash": hash, "typed": typed, "ok": ok, "reply": {"token": "a" * 43}, "storage": storage})}
    run = subprocess.run([node, "-e", _PAGE_HARNESS + script], env=env, capture_output=True, text=True,
                         timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_launch_page_enters_the_webapp_with_no_session_in_any_url(monkeypatch):
    """The page sends the typed code in a header and hands the session over in this tab's
    sessionStorage, as the child-window handoff does: a ``#hermes-session=`` target stays in browser
    history (#93508, Lane B P3 #4)."""
    token, key = "a" * 43, 'hermes.webapp.session.v1:["http://127.0.0.1:9123",""]'
    entered = _run_launch_page(monkeypatch, typed=" abcde-fghij ", hash="#query=%3Fprofile%3Dcoder")
    assert entered["sent"] == [{"X-Hermes-Launch-Code": "ABCDEFGHIJ"}]
    assert entered["replaced"] == "http://127.0.0.1:9123/?profile=coder"
    assert (entered["stored"], entered["windowName"]) == ({key: token}, "")
    # A browser that blocks storage still gets in: the token crosses in this tab's memory, not a URL.
    blocked = _run_launch_page(monkeypatch, typed="ABCDE-FGHIJ", storage=False)
    assert (blocked["replaced"], blocked["windowName"]) == ("http://127.0.0.1:9123/", f"{key}={token}")
    refused = _run_launch_page(monkeypatch, typed="ABCDE-FGHIJ", ok=False)
    assert refused["replaced"] is None and not refused["stored"] and "run hermes webapp again" in refused["status"]
