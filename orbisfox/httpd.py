"""The panel's web server: serves the UI, a JSON API and a live event stream (Server-Sent Events).

Security model
- By default the panel listens on 127.0.0.1 only. The page on this PC gets an API token from
  /api/token, which answers only loopback clients whose Host header is a loopback name (so another
  website cannot read it, even through DNS rebinding).
- With LAN access turned on, other devices sign in with the panel password and receive the same
  token. Every API call must carry the token in the X-OrbisFox-Token header (the event stream, which
  cannot set headers, takes it as ?token=); cross-site pages cannot add that header.
"""
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import queue
import secrets
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return h, salt


class Router:
    def __init__(self):
        self.routes = {}

    def add(self, method, path, fn):
        self.routes[(method, path)] = fn

    def get(self, path):
        return lambda fn: (self.add("GET", path, fn), fn)[1]

    def post(self, path):
        return lambda fn: (self.add("POST", path, fn), fn)[1]


class PanelServer:
    def __init__(self, core, router):
        self.core = core
        self.router = router
        self.token = secrets.token_urlsafe(32)
        self._fails = {}   # ip -> (count, first_time) for login throttling
        self.httpd = None

    def is_loopback(self, ip):
        try:
            return ipaddress.ip_address(ip.split("%")[0]).is_loopback
        except ValueError:
            return False

    def start(self, port, lan):
        host = "0.0.0.0" if lan else "127.0.0.1"
        panel = self

        class Handler(_Handler):
            server_ref = panel

        self.httpd = ThreadingHTTPServer((host, port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="http", daemon=True).start()
        return self.httpd.server_address

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def check_password(self, ip, password):
        cnt, first = self._fails.get(ip, (0, time.time()))
        if time.time() - first > 600:
            cnt, first = 0, time.time()
        if cnt >= 8:
            raise ApiError(429, "Too many attempts. Try again in a few minutes.")
        p = self.core.settings["panel"]
        if not p.get("password_hash"):
            raise ApiError(403, "Set a panel password on the server PC first.")
        h, _ = hash_password(password or "", p["salt"])
        if not hmac.compare_digest(h, p["password_hash"]):
            self._fails[ip] = (cnt + 1, first)
            raise ApiError(401, "Wrong password.")
        self._fails.pop(ip, None)
        return self.token


class _Handler(BaseHTTPRequestHandler):
    server_ref: PanelServer = None
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    # -- helpers ---------------------------------------------------------------------------
    @property
    def panel(self):
        return self.server_ref

    def _client_ip(self):
        return self.client_address[0]

    def _host_ok(self):
        """Loopback clients must address us by a loopback name (stops DNS rebinding)."""
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
        if self.panel.is_loopback(self._client_ip()):
            return host in LOOPBACK_HOSTS
        return self.panel.core.settings["panel"].get("lan", False)

    def _send(self, status, body=b"", ctype="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if "Cache-Control" not in (extra or {}):
            self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; "
                             "connect-src 'self'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status, obj):
        self._send(status, json.dumps(obj, default=str).encode())

    def _authed(self, query):
        tok = self.headers.get("X-OrbisFox-Token") or (query.get("token") or [""])[0]
        return bool(tok) and hmac.compare_digest(tok, self.panel.token)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ApiError(413, "Request too large.")
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError:
            raise ApiError(400, "Body must be JSON.")

    # -- verbs -----------------------------------------------------------------------------
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        try:
            if not self._host_ok():
                raise ApiError(403, "Forbidden host.")
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError(405, "Method not allowed.")
                return self._static(path)
            if path == "/api/token" and method == "GET":
                if not self.panel.is_loopback(self._client_ip()):
                    raise ApiError(401, "Sign in required.")
                return self._json(200, {"token": self.panel.token})
            if path == "/api/login" and method == "POST":
                body = self._body()
                return self._json(200, {"token": self.panel.check_password(self._client_ip(), body.get("password"))})
            if path == "/api/hello":   # unauthenticated: does this panel want a password?
                return self._json(200, {"app": "OrbisFox", "local": self.panel.is_loopback(self._client_ip())})
            if not self._authed(query):
                raise ApiError(401, "Sign in required.")
            if path == "/api/events":
                return self._events()
            fn = self.panel.router.routes.get((method, path))
            if not fn:
                raise ApiError(404, "No such endpoint.")
            body = self._body() if method == "POST" else {}
            query["_local"] = ["1" if self.panel.is_loopback(self._client_ip()) else ""]
            result = fn(self.panel.core, body, query)
            if isinstance(result, tuple) and len(result) == 3 and result[0] == "file":
                return self._file(result[1], result[2])
            if isinstance(result, tuple) and len(result) == 3 and result[0] == "bytes":
                return self._send(200, result[1], result[2], {"Cache-Control": "max-age=30"})
            return self._json(200, result if result is not None else {"ok": True})
        except ApiError as e:
            self._json(e.status, {"error": str(e)})
        except (ValueError, RuntimeError, FileNotFoundError, PermissionError) as e:
            self._json(400, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:  # noqa: BLE001 - report, never crash the panel
            traceback.print_exc()
            try:
                self._json(500, {"error": f"{type(e).__name__}: {e}"})
            except OSError:
                pass

    def _static(self, path):
        if path in ("", "/"):
            path = "/index.html"
        full = os.path.realpath(os.path.join(WEB_DIR, path.lstrip("/")))
        if not full.startswith(os.path.realpath(WEB_DIR) + os.sep) or not os.path.isfile(full):
            raise ApiError(404, "Not found.")
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, ctype)

    def _file(self, path, download_name):
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.end_headers()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _events(self):
        core = self.panel.core
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        q = core.hub.subscribe()
        try:
            snap = json.dumps(core.snapshot(), default=str)
            self.wfile.write(f"event: snapshot\ndata: {snap}\n\n".encode())
            self.wfile.flush()
            while True:
                try:
                    kind, data = q.get(timeout=15)
                    chunk = f"event: {kind}\ndata: {data}\n\n"
                    # batch whatever else is waiting, to keep busy consoles cheap
                    for _ in range(200):
                        try:
                            kind, data = q.get_nowait()
                        except queue.Empty:
                            break
                        chunk += f"event: {kind}\ndata: {data}\n\n"
                    self.wfile.write(chunk.encode())
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass
        finally:
            core.hub.unsubscribe(q)
            self.close_connection = True
