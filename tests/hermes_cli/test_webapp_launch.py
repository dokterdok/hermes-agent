"""Operator handoff must not turn the launch credential into process-list data."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

import pytest


@pytest.fixture
def launcher(monkeypatch, tmp_path):
    """A loopback Webapp's private launch, its scheduled browser opener run inline and recorded."""
    import webbrowser
    from hermes_cli import web_server as server, web_server_lifecycle as lifecycle

    monkeypatch.setattr(server.app.state, "ui_surface", "webapp", raising=False)
    monkeypatch.setattr(server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(server.app.state, "webapp_window_tickets", {}, raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("DISPLAY", ":test")
    # Execute the scheduled opener deterministically, without launching a browser.
    monkeypatch.setattr(lifecycle.threading, "Thread", lambda target, **kw: SimpleNamespace(start=target))
    monkeypatch.setattr(lifecycle.time, "sleep", lambda seconds: None)
    opened = []
    monkeypatch.setattr(webbrowser, "open", opened.append)
    return SimpleNamespace(server=server, lifecycle=lifecycle, opened=opened)


def _printed_launch_link(out: str) -> str:
    return out.split(": ", 1)[1].split()[0]


def test_private_launch_uses_existing_token_and_never_hands_it_to_the_browser_launcher(launcher, monkeypatch, capsys, caplog):
    server, lifecycle, opened = launcher.server, launcher.lifecycle, launcher.opened
    lifecycle._maybe_open_browser("::1", 9123, False, "coder")
    launch = _printed_launch_link(capsys.readouterr().out)
    parsed = urlsplit(launch)
    assert parsed.hostname == "::1"
    assert parse_qs(parsed.query) == {"profile": ["coder"]}
    assert parse_qs(parsed.fragment) == {"hermes-session": [server._SESSION_TOKEN]}
    assert not opened
    assert server._SESSION_TOKEN not in caplog.text

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


@pytest.mark.platforms("linux", "windows")
def test_private_auto_open_hands_the_launcher_neither_the_token_nor_a_home_path(launcher, tmp_path, capsys, caplog):
    launcher.lifecycle._maybe_open_browser("::1", 9123, True, "coder")
    capsys.readouterr()
    assert len(launcher.opened) == 1 and launcher.server._SESSION_TOKEN not in launcher.opened[0]
    assert str(tmp_path) not in launcher.opened[0]
    assert launcher.server._SESSION_TOKEN not in caplog.text


@pytest.mark.platforms("macos")
def test_macos_private_launch_opens_no_browser_and_prints_the_link(launcher, capsys):
    """macOS launch arguments are no same-user credential channel: the operator gets the link."""
    launcher.lifecycle._maybe_open_browser("::1", 9123, True, "coder")
    out = capsys.readouterr().out
    assert launcher.opened == []
    fragment = urlsplit(_printed_launch_link(out)).fragment
    assert parse_qs(fragment) == {"hermes-session": [launcher.server._SESSION_TOKEN]}
    assert "open the private launch link above in your browser" in out


_SERVER = """
import os, sys
sys.path.insert(0, os.getcwd())
from hermes_cli.web_server import start_server
start_server(host="127.0.0.1", port=0, open_browser=True, ui_surface="webapp")
"""


@pytest.mark.platforms("linux")
def test_auto_open_hands_the_browser_a_one_use_launch_url_any_sandboxed_browser_can_follow(tmp_path):
    """Real server, real browser dispatch: the URL a launcher receives (the argv other local
    users may read) carries neither the session token nor a Hermes-home file a snap/Flatpak
    browser cannot open. Following it once enters the private session; a replay gets nothing."""
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
               HERMES_DESKTOP_READY_FILE=str(ready), BROWSER=str(browser), DISPLAY=":launch-test")
    log_path = tmp_path / "server.log"
    with log_path.open("w", encoding="utf-8") as log:
        server = subprocess.Popen([sys.executable, "-u", "-c", _SERVER], cwd=Path(__file__).resolve().parents[2],
                                  env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.monotonic() + 60
        while not (recorded.exists() and recorded.stat().st_size) and server.poll() is None \
                and time.monotonic() < deadline:
            time.sleep(0.1)
        output = log_path.read_text(encoding="utf-8-sig", errors="replace")
        assert recorded.exists() and recorded.stat().st_size and ready.exists(), output
        port = json.loads(ready.read_text(encoding="utf-8-sig"))["port"]
        token = parse_qs(urlsplit(output.split("grants host access): ", 1)[1].split()[0]).fragment)["hermes-session"][0]

        launched = recorded.read_text(encoding="utf-8-sig").split()[-1]
        assert token not in launched and str(tmp_path) not in launched
        target = urlsplit(launched)
        assert (target.scheme, target.hostname, target.port) == ("http", "127.0.0.1", port)

        origin = f"http://127.0.0.1:{port}"
        with urlopen(f"{origin}{target.path}", timeout=10) as page:
            assert page.status == 200 and token not in page.read().decode()
        exchange = Request(f"{origin}/webapp/window-session", data=b"", method="POST", headers={
            "Origin": origin, "X-Hermes-Window-Ticket": parse_qs(target.fragment)["ticket"][0]})
        with urlopen(exchange, timeout=10) as granted:
            assert json.loads(granted.read())["token"] == token
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


@pytest.mark.platforms("linux")
def test_launch_ticket_is_spent_only_over_the_server_users_own_connection(monkeypatch):
    """Linux publishes every process's argv, so another local user can read a launch ticket
    out of the browser launcher before the browser spends it. That user's connection is
    refused, and the refusal does not burn the ticket for the operator's browser."""
    from fastapi.testclient import TestClient
    from hermes_cli import web_server as server
    from hermes_cli.web_routers import webapp

    monkeypatch.setattr(server.app.state, "ui_surface", "webapp", raising=False)
    monkeypatch.setattr(server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(server.app.state, "bound_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(server.app.state, "webapp_window_tickets", {}, raising=False)
    monkeypatch.setattr(server, "_SESSION_TOKEN", "a" * 43)
    client = TestClient(server.app, base_url="http://127.0.0.1")
    headers = {"Origin": "http://127.0.0.1", "X-Hermes-Window-Ticket": webapp.mint_launch_ticket(server.app.state)}

    own_uid = os.getuid()  # windows-footgun: ok — platforms("linux") test
    monkeypatch.setattr(webapp, "_loopback_peer_uid", lambda client, server: own_uid + 1)
    stolen = client.post("/webapp/window-session", headers=headers)
    assert stolen.status_code == 403 and "a" * 43 not in stolen.text
    monkeypatch.setattr(webapp, "_loopback_peer_uid", lambda client, server: own_uid)
    granted = client.post("/webapp/window-session", headers=headers)
    assert granted.status_code == 200 and granted.json()["token"] == "a" * 43


@pytest.mark.platforms("macos")
def test_macos_launch_handoff_fails_closed_without_consuming_child_window_authority(monkeypatch):
    """An argv launch ticket is unusable until macOS has an authenticated OS handoff.

    This native platform contract makes no claim about cross-account argv visibility.
    Authenticated child-window tickets never pass through a launcher and still work.
    """
    from fastapi.testclient import TestClient
    from hermes_cli import web_server as server
    from hermes_cli.web_routers import webapp

    monkeypatch.setattr(server.app.state, "ui_surface", "webapp", raising=False)
    monkeypatch.setattr(server.app.state, "auth_required", False, raising=False)
    monkeypatch.setattr(server.app.state, "bound_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(server.app.state, "webapp_window_tickets", {}, raising=False)
    monkeypatch.setattr(server, "_SESSION_TOKEN", "a" * 43)
    client = TestClient(server.app, base_url="http://127.0.0.1")
    try:
        launch = webapp.mint_launch_ticket(server.app.state)
        headers = {"Origin": "http://127.0.0.1", "X-Hermes-Window-Ticket": launch}
        for _ in range(2):
            denied = client.post("/webapp/window-session", headers=headers)
            assert denied.status_code == 403 and server._SESSION_TOKEN not in denied.text
            assert launch in server.app.state.webapp_window_tickets

        issued = client.post("/api/webapp/window-ticket", headers={
            server._SESSION_HEADER_NAME: server._SESSION_TOKEN,
        })
        assert issued.status_code == 200
        headers["X-Hermes-Window-Ticket"] = issued.json()["ticket"]
        assert client.post("/webapp/window-session", headers=headers).json() == {"token": server._SESSION_TOKEN}
        assert client.post("/webapp/window-session", headers=headers).status_code == 403
    finally:
        client.close()


# A served handoff page's script, run as a browser tab runs it, with the browser globals it touches
# stubbed: what it kept in sessionStorage, the headers it sent, and where it navigated.
_PAGE_HARNESS = r"""
const env = JSON.parse(process.env.PAGE_ENV);
const out = {stored: {}, sent: [], replaced: null};
const stub = (name, value) => Object.defineProperty(globalThis, name, {value, configurable: true, writable: true});
stub('location', {hash: env.hash, pathname: '/webapp/launch', origin: 'http://127.0.0.1:9123',
                  replace: url => { out.replaced = url; }});
stub('history', {replaceState() {}});
stub('window', {name: ''});
stub('document', {getElementById: () => ({})});
stub('sessionStorage', {setItem(key, value) { if (!env.storage) throw new Error('blocked'); out.stored[key] = value; }});
stub('fetch', async (url, init) => { out.sent.push(init.headers); return {ok: true, json: async () => env.reply}; });
process.once('beforeExit', () => { out.windowName = window.name; console.log(JSON.stringify(out)); });
"""


def _run_launch_page(monkeypatch, *, hash, reply, storage=True):
    from fastapi.testclient import TestClient
    from hermes_cli import web_server as server

    node = shutil.which("node")
    if node is None:
        pytest.skip("running the served launch page needs node")
    for name, value in (("ui_surface", "webapp"), ("auth_required", False), ("bound_host", "127.0.0.1")):
        monkeypatch.setattr(server.app.state, name, value, raising=False)
    page = TestClient(server.app, base_url="http://127.0.0.1").get("/webapp/launch").text
    script = re.search(r'<script nonce="[^"]+">(.*)</script>', page, re.DOTALL).group(1)
    env = {**os.environ, "PAGE_ENV": json.dumps({"hash": hash, "reply": reply, "storage": storage})}
    run = subprocess.run([node, "-e", _PAGE_HARNESS + script], env=env, capture_output=True, text=True,
                         timeout=60, check=False)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_launch_page_enters_the_webapp_with_no_session_in_any_url(monkeypatch):
    """The auto-opened page hands the session over in this tab's sessionStorage, as the child-window
    handoff does: a ``#hermes-session=`` target stays in browser history (#93508, Lane B P3 #4)."""
    token, key = "a" * 43, 'hermes.webapp.session.v1:["http://127.0.0.1:9123",""]'
    entered = _run_launch_page(monkeypatch, hash=f"#ticket={'t' * 43}&query=%3Fprofile%3Dcoder", reply={"token": token})
    assert entered["replaced"] == "http://127.0.0.1:9123/?profile=coder"
    assert (entered["stored"], entered["windowName"]) == ({key: token}, "")
    # A browser that blocks storage still gets in: the token crosses in this tab's memory, not a URL.
    blocked = _run_launch_page(monkeypatch, hash=f"#ticket={'t' * 43}", reply={"token": token}, storage=False)
    assert (blocked["replaced"], blocked["windowName"]) == ("http://127.0.0.1:9123/", f"{key}={token}")
