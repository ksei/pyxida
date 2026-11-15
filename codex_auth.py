"""Codex sign-in from the browser using the CLI's device-code flow.

`codex login --device-auth` prints a verification link and a one-time code, then
waits until the user approves the code on any device. This works in a container
or on a server, where the normal localhost callback login cannot reach the
user's browser. Credentials are written by the CLI to CODEX_HOME; this module
never reads or returns them.
"""
import os
import re
import signal
import subprocess
import threading
import time

from ai import WorkflowError, codex_binary, codex_environment, codex_status

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
LINK = re.compile(r"https://[^\s]+")
CODE = re.compile(r"\b[A-Z0-9]{4}-[A-Z0-9]{4,6}\b")
LOGIN_TIMEOUT = 15 * 60


class DeviceLogin:
    def __init__(self, popen=subprocess.Popen, status=codex_status):
        self.popen = popen
        self.status = status
        self.lock = threading.Lock()
        self.process = None
        self.state = {"status": "idle"}
        self.ready = threading.Event()

    def snapshot(self):
        with self.lock:
            state = dict(self.state)
        if state["status"] == "waiting" and time.time() - state["started"] > LOGIN_TIMEOUT:
            self.cancel("expired")
            return self.snapshot()
        return state

    def start(self, wait=15):
        with self.lock:
            if self.process and self.process.poll() is None:
                return dict(self.state)
            binary = codex_binary()
            if not binary:
                raise WorkflowError("The Codex CLI is not installed in this environment.")
            self.ready.clear()
            try:
                self.process = self.popen([binary, "login", "--device-auth"], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                          env=codex_environment(), start_new_session=True)
            except OSError as e:
                raise WorkflowError("Could not start the Codex sign-in.") from e
            self.state = {"status": "starting", "url": "", "code": "", "message": "", "started": time.time()}
            process = self.process
        threading.Thread(target=self._watch, args=(process,), daemon=True).start()
        self.ready.wait(wait)
        return self.snapshot()

    def _watch(self, process):
        tail = []
        for raw in process.stdout:
            line = ANSI.sub("", raw).strip()
            if not line:
                continue
            tail = (tail + [line])[-6:]
            with self.lock:
                if process is not self.process:
                    return
                if not self.state["url"] and (match := LINK.search(line)):
                    self.state["url"] = match.group(0).rstrip(".,)")
                if not self.state["code"] and (match := CODE.search(line)):
                    self.state["code"] = match.group(0)
                if self.state["url"] and self.state["code"] and self.state["status"] == "starting":
                    self.state["status"] = "waiting"
                    self.ready.set()
        code = process.wait()
        signed_in = code == 0 and self.status()["configured"]
        with self.lock:
            if process is not self.process or self.state["status"] in ("cancelled", "expired"):
                self.ready.set()
                return
            if signed_in:
                self.state.update(status="success", url="", code="", message="Codex is connected to your ChatGPT account.")
            else:
                detail = " ".join(l for l in tail if "code" not in l.lower())[-300:]
                self.state.update(status="failed", url="", code="",
                                  message="The sign-in did not complete." + (f" Codex said: {detail}" if detail else ""))
        self.ready.set()

    def cancel(self, reason="cancelled"):
        with self.lock:
            process = self.process
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    pass
            if self.state["status"] in ("starting", "waiting"):
                self.state.update(status=reason, url="", code="",
                                  message="The sign-in code expired. Start again." if reason == "expired" else "Sign-in cancelled.")
            return dict(self.state)


def logout():
    binary = codex_binary()
    if not binary:
        raise WorkflowError("The Codex CLI is not installed in this environment.")
    try:
        subprocess.run([binary, "logout"], capture_output=True, text=True, timeout=30, env=codex_environment())
    except (OSError, subprocess.TimeoutExpired) as e:
        raise WorkflowError("Could not sign out of Codex.") from e
    return codex_status()
