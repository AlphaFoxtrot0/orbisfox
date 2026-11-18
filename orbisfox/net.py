"""HTTP downloads that also work on PCs where Python can't verify a site's certificate chain.

Python's TLS does not fetch missing intermediate certificates the way Windows does, so on some
PCs (and behind some security software) a perfectly good HTTPS site fails with
CERTIFICATE_VERIFY_FAILED. In that case the request is retried with curl, which ships with
Windows 10 and later and uses Windows' own certificate handling. Verification stays on either way.
"""
import os
import shutil
import ssl
import subprocess
import urllib.error
import urllib.request

UA = {"User-Agent": "OrbisFox"}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _cert_problem(e):
    reason = getattr(e, "reason", e)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)


def _curl():
    return shutil.which("curl.exe" if os.name == "nt" else "curl")


def fetch(url, timeout=20):
    """Returns the response body as bytes."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
            return r.read()
    except urllib.error.URLError as e:
        curl = _curl()
        if not (_cert_problem(e) and curl):
            raise
        out = subprocess.run([curl, "-fsSL", "-A", "OrbisFox", "--max-time", str(timeout), url],
                             capture_output=True, timeout=timeout + 5, creationflags=NO_WINDOW)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.decode("utf-8", "replace").strip() or f"curl failed ({out.returncode})")
        return out.stdout


def download(url, dest, timeout=600):
    """Saves a URL to a file."""
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.URLError as e:
        curl = _curl()
        if not (_cert_problem(e) and curl):
            raise
        out = subprocess.run([curl, "-fsSL", "-A", "OrbisFox", "--max-time", str(timeout), "-o", dest, url],
                             capture_output=True, timeout=timeout + 10, creationflags=NO_WINDOW)
        if out.returncode != 0:
            raise RuntimeError(out.stderr.decode("utf-8", "replace").strip() or f"curl failed ({out.returncode})")
