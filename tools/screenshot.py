"""Takes README screenshots of a running OrbisFox (usually the demo) with headless Edge or Chrome.

    python tools/screenshot.py http://localhost:8770/ docs/img/overview.png [--js "window.orbis.openPanel('map')"] [--wait 6]

Talks to the browser over the DevTools protocol with a minimal WebSocket client (standard library only).
"""
import argparse
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from orbisfox.desktop import _browser  # noqa: E402


class WS:
    def __init__(self, url):
        assert url.startswith("ws://")
        hostport, path = url[5:].split("/", 1)
        host, port = hostport.split(":")
        self.s = socket.create_connection((host, int(port)))
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            resp += self.s.recv(1)
        if b" 101 " not in resp.split(b"\r\n")[0]:
            raise RuntimeError(resp.decode(errors="replace"))
        self.n = 0

    def send(self, obj):
        data = json.dumps(obj).encode()
        hdr = bytearray([0x81])
        ln = len(data)
        if ln < 126:
            hdr.append(0x80 | ln)
        elif ln < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", ln)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", ln)
        mask = os.urandom(4)
        hdr += mask
        self.s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise EOFError
            buf += chunk
        return buf

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._exact(2)
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._exact(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._exact(8))[0]
            msg += self._exact(ln)
            if b1 & 0x80:
                return json.loads(msg)

    def call(self, method, **params):
        self.n += 1
        my = self.n
        self.send({"id": my, "method": method, "params": params})
        while True:
            m = self.recv()
            if m.get("id") == my:
                if "error" in m:
                    raise RuntimeError(m["error"])
                return m.get("result", {})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("out")
    ap.add_argument("--js", default="")
    ap.add_argument("--wait", type=float, default=6)
    ap.add_argument("--width", type=int, default=1600)
    ap.add_argument("--height", type=int, default=960)
    a = ap.parse_args()
    exe = _browser()
    if not exe:
        sys.exit("Edge or Chrome not found")
    port = 9333
    prof = tempfile.mkdtemp(prefix="orbisfox-shot-")
    proc = subprocess.Popen([exe, "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={prof}",
                             "--hide-scrollbars", "--no-first-run", f"--window-size={a.width},{a.height}", "about:blank"])
    try:
        for _ in range(50):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1))
                page = next(t for t in targets if t.get("type") == "page")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2)
        ws = WS(page["webSocketDebuggerUrl"])
        ws.call("Emulation.setDeviceMetricsOverride", width=a.width, height=a.height, deviceScaleFactor=1, mobile=False)
        ws.call("Page.enable")
        ws.call("Page.navigate", url=a.url)
        time.sleep(a.wait)
        if a.js:
            ws.call("Runtime.evaluate", expression=a.js)
            time.sleep(a.wait)
        shot = ws.call("Page.captureScreenshot", format="png")
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("saved", a.out)
    finally:
        proc.kill()


if __name__ == "__main__":
    main()
