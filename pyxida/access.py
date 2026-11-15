"""Optional password access for network deployments (containers, home servers).

Sessions are HMAC-signed cookies keyed by a random secret stored in the data
directory and by the password itself, so changing APP_PASSWORD signs everyone
out and restarts keep people signed in. Failed attempts are rate limited per
client address.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time
from pathlib import Path

COOKIE = "pyxida_session"
SESSION_DAYS = 30
MAX_FAILURES = 8
LOCKOUT_SECONDS = 300


class Access:
    def __init__(self, password, directory):
        self.password = password or ""
        self.enabled = bool(self.password)
        self.failures = {}
        self.lock = threading.Lock()
        self.key = b""
        if self.enabled:
            path = Path(directory) / "session.key"
            if not path.exists():
                path.write_bytes(secrets.token_bytes(32))
                path.chmod(0o600)
            self.key = hmac.new(path.read_bytes(), self.password.encode(), hashlib.sha256).digest()

    def _sign(self, issued):
        return hmac.new(self.key, str(issued).encode(), hashlib.sha256).hexdigest()

    def issue(self):
        issued = int(time.time())
        return f"{issued}.{self._sign(issued)}"

    def valid(self, cookie_header):
        if not self.enabled:
            return True
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name != COOKIE:
                continue
            issued, _, signature = value.partition(".")
            if (issued.isdigit() and 0 <= time.time() - int(issued) <= SESSION_DAYS * 86400
                    and hmac.compare_digest(signature, self._sign(int(issued)))):
                return True
        return False

    def locked(self, client):
        with self.lock:
            count, until = self.failures.get(client, (0, 0))
            return count >= MAX_FAILURES and time.time() < until

    def attempt(self, client, password):
        if self.locked(client):
            return False
        ok = hmac.compare_digest(hashlib.sha256(password.encode()).digest(),
                                 hashlib.sha256(self.password.encode()).digest())
        with self.lock:
            if ok:
                self.failures.pop(client, None)
            else:
                count, _ = self.failures.get(client, (0, 0))
                self.failures[client] = (count + 1, time.time() + LOCKOUT_SECONDS)
        if not ok:
            time.sleep(0.5)
        return ok

    def cookie(self, value, secure, max_age=SESSION_DAYS * 86400):
        flags = f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"
        return flags + ("; Secure" if secure else "")


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Pyxida · Sign in</title>
<link rel="stylesheet" href="/styles.css"><script src="/theme.js"></script></head><body class="login-page"><main class="login-card">
<a class="brand"><span class="brand-mark">p.</span><span>pyxida<small>YOUR CAREER COMPASS</small></span></a>
<h1>Sign in</h1><p class="small">This Pyxida instance is protected with a password.</p>
<form method="post" action="/login"><label>Password <input type="password" name="password" autocomplete="current-password" required autofocus></label>
{error}<button class="primary" type="submit">Continue <span>↗</span></button></form></main></body></html>"""


def login_page(error=""):
    message = f'<p class="notice error">{error}</p>' if error else ""
    return LOGIN_PAGE.replace("{error}", message).encode()


def env_flag(name):
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")
