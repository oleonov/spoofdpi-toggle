"""Live end-to-end check of the installed native host. Starts the real spoofdpi on port 18080.

Run after install.sh: /usr/bin/python3 -I tests/e2e_native.py
"""
import json
import os
import queue
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

LAUNCHER = os.path.expanduser("~/Library/Application Support/spoofdpi-toggle/host.sh")
PORT = 18080
REPLY_TIMEOUT = 15
EXIT_TIMEOUT = 10
CURL = [
    "/usr/bin/curl", "-sS", "-m", "20", "-o", "/dev/null", "-w", "%{http_code}",
    "-x", "http://127.0.0.1:%d" % PORT, "https://www.linkedin.com",
]


class StepFailed(Exception):
    pass


def check(name, condition, detail=""):
    print("%s  %s%s" % ("PASS" if condition else "FAIL", name, (": " + detail) if detail else ""), flush=True)
    if not condition:
        raise StepFailed(name)


def port_open():
    try:
        socket.create_connection(("127.0.0.1", PORT), timeout=0.5).close()
        return True
    except OSError:
        return False


def spoofdpi_running():
    return subprocess.run(["/usr/bin/pgrep", "-x", "spoofdpi"], stdout=subprocess.DEVNULL).returncode == 0


def wait_until(condition, timeout):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


class Host:
    """Talks to the launcher the way Firefox does: framed JSON over stdin/stdout pipes."""

    def __init__(self):
        self.proc = subprocess.Popen([LAUNCHER], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.messages = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            header = self.proc.stdout.read(4)
            if len(header) < 4:
                return
            body = self.proc.stdout.read(struct.unpack("=I", header)[0])
            self.messages.put(json.loads(body.decode("utf-8")))

    def request(self, cmd):
        data = json.dumps({"cmd": cmd}).encode("utf-8")
        self.proc.stdin.write(struct.pack("=I", len(data)) + data)
        self.proc.stdin.flush()
        try:
            return self.messages.get(timeout=REPLY_TIMEOUT)
        except queue.Empty:
            return None

    def close_stdin_and_wait(self):
        self.proc.stdin.close()
        try:
            return self.proc.wait(timeout=EXIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            return None

    def shutdown(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)  # lets the host stop spoofdpi first
            try:
                self.proc.wait(timeout=EXIT_TIMEOUT)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()


def assert_spoofdpi_gone(prefix):
    check(prefix + ": port %d closed" % PORT, wait_until(lambda: not port_open(), 5))
    check(prefix + ": no spoofdpi process", wait_until(lambda: not spoofdpi_running(), 5))


def start(host, prefix):
    reply = host.request("start")
    check(prefix + ": start -> ok:true", reply == {"ok": True}, repr(reply))


def scenario_start_curl_stop():
    host = Host()
    try:
        start(host, "stop scenario")
        curl = subprocess.run(CURL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        code = curl.stdout.decode().strip()
        check("stop scenario: linkedin.com through proxy -> 200", code == "200",
              code + " " + curl.stderr.decode().strip())
        reply = host.request("status")
        check("stop scenario: status -> running", reply == {"ok": True, "running": True}, repr(reply))
        reply = host.request("stop")
        check("stop scenario: stop -> ok:true", reply == {"ok": True}, repr(reply))
        assert_spoofdpi_gone("stop scenario")
        code = host.close_stdin_and_wait()
        check("stop scenario: host exits 0 on EOF", code == 0, repr(code))
    finally:
        host.shutdown()


def scenario_eof_without_stop():
    host = Host()
    try:
        start(host, "eof scenario")
        code = host.close_stdin_and_wait()
        check("eof scenario: host exits 0 on EOF", code == 0, repr(code))
        assert_spoofdpi_gone("eof scenario")
    finally:
        host.shutdown()


def main():
    try:
        check("launcher installed", os.access(LAUNCHER, os.X_OK), LAUNCHER)
        check("precondition: port %d free" % PORT, not port_open())
        check("precondition: no spoofdpi process", not spoofdpi_running())
        scenario_start_curl_stop()
        scenario_eof_without_stop()
    except (StepFailed, subprocess.TimeoutExpired) as e:
        print("FAIL  e2e stopped at: %s" % e, flush=True)
        return 1
    print("PASS  all steps", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
