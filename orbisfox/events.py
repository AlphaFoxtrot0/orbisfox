"""A tiny publish/subscribe hub. Every open panel (browser window) gets its own queue and receives
events as Server-Sent Events."""
import json
import queue
import threading


class Hub:
    def __init__(self):
        self._lock = threading.Lock()
        self._subs = set()

    def subscribe(self):
        q = queue.Queue(maxsize=5000)
        with self._lock:
            self._subs.add(q)
        return q

    def unsubscribe(self, q):
        with self._lock:
            self._subs.discard(q)

    def publish(self, kind, data):
        msg = (kind, json.dumps(data, separators=(",", ":"), default=str))
        with self._lock:
            subs = list(self._subs)
        for q in subs:
            try:
                q.put_nowait(msg)
            except queue.Full:   # a stalled client; drop it rather than block the server
                self.unsubscribe(q)

    @property
    def clients(self):
        with self._lock:
            return len(self._subs)
