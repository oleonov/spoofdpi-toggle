"""Firefox native messaging host that starts and stops a local SpoofDPI proxy."""
import glob
import json
import os
import select
import shlex
import signal
import socket
import struct
import subprocess
import sys
import threading
import time

SPOOFDPI_BIN = os.environ.get("SPOOFDPI_BIN", "/opt/homebrew/bin/spoofdpi")
PORT = int(os.environ.get("SPOOFDPI_TOGGLE_PORT", "18080"))
STATE_GLOB = os.environ.get("SPOOFDPI_TOGGLE_STATE_GLOB", "/tmp/spoofdpi*.state")
LOG_PATH = os.environ.get(
    "SPOOFDPI_TOGGLE_LOG", os.path.expanduser("~/Library/Logs/spoofdpi-toggle.log"))
PID_PATH = os.environ.get(
    "SPOOFDPI_TOGGLE_PIDFILE", os.path.expanduser("~/Library/Caches/spoofdpi-toggle/spoofdpi.pid"))

LISTEN_HOST = "127.0.0.1"
PROCESS_NAME = "spoofdpi"
START_TIMEOUT = 5.0
STOP_TIMEOUT = 3.0
POLL_INTERVAL = 0.05
SIGNAL_POLL_INTERVAL = 0.2
EXIT_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

# --clean keeps spoofdpi from loading config files that could reconfigure the macOS system proxy.
FIXED_ARGS = ["--clean", "--no-tui", "--listen-addr", "%s:%d" % (LISTEN_HOST, PORT)]
# Tuning that works against the author's ISP; SPOOFDPI_ARGS replaces it as a whole.
DEFAULT_TUNING_ARGS = [
    "--dns-mode", "https",
    "--dns-https-url", "https://cloudflare-dns.com/dns-query",
    "--https-split-mode", "chunk",
    "--https-chunk-size", "1",
]
# Flags that would break the fixed listener, load a config file or touch the system proxy.
# Matched without leading dashes, because spoofdpi accepts both -flag and --flag.
FORBIDDEN_FLAGS = frozenset(
    ["clean", "no-tui", "listen-addr", "app-mode", "config", "c", "auto-configure-network"])

OK = {"ok": True}
STATE_FILE_ERROR = (
    "SpoofDPI system-proxy state file %s exists from another run; start spoofdpi manually once "
    "(it restores and removes it) or delete the file")


def fail(error):
    return {"ok": False, "error": error}


def read_exactly(fd, size):
    data = b""
    while len(data) < size:
        # The kernel may hand a signal to the monitor thread; its Python handler runs only once the
        # main thread executes bytecode again, so never block on stdin indefinitely.
        if not select.select([fd], [], [], SIGNAL_POLL_INTERVAL)[0]:
            continue
        chunk = os.read(fd, size - len(data))
        if not chunk:
            return None
        data += chunk
    return data


def read_message(fd):
    header = read_exactly(fd, 4)
    if header is None:
        return None
    body = read_exactly(fd, struct.unpack("=I", header)[0])
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return {}


def wait_until(condition, timeout):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(POLL_INTERVAL)
    return True


def terminate(send_signal, has_exited):
    send_signal(signal.SIGTERM)
    if not wait_until(has_exited, STOP_TIMEOUT):
        send_signal(signal.SIGKILL)


def port_accepts():
    try:
        socket.create_connection((LISTEN_HOST, PORT), timeout=0.2).close()
        return True
    except OSError:
        return False


def process_name(pid):
    result = subprocess.run(
        ["/bin/ps", "-p", str(pid), "-o", "comm="], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return os.path.basename(result.stdout.decode("utf-8", "replace").strip())


def signal_pid(pid, sig):
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass


def kill_stale_spoofdpi():
    """A host killed without cleanup leaves its child (own session) running; stop it."""
    try:
        with open(PID_PATH) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        pid = None
    if pid and process_name(pid) == PROCESS_NAME:
        terminate(lambda sig: signal_pid(pid, sig), lambda: process_name(pid) != PROCESS_NAME)
    remove_pid_file()


def remove_pid_file():
    try:
        os.remove(PID_PATH)
    except FileNotFoundError:
        pass


def precheck_error():
    state_files = sorted(glob.glob(STATE_GLOB))
    if state_files:
        return STATE_FILE_ERROR % state_files[0]
    kill_stale_spoofdpi()
    if port_accepts():
        return "port %d is busy" % PORT
    if not (os.path.isfile(SPOOFDPI_BIN) and os.access(SPOOFDPI_BIN, os.X_OK)):
        return "spoofdpi not found at %s" % SPOOFDPI_BIN
    return None


def spoofdpi_argv():
    """Returns (argv, error); SPOOFDPI_ARGS is a shell-quoted string of tuning flags."""
    text = os.environ.get("SPOOFDPI_ARGS", "").strip()
    if not text:
        return [SPOOFDPI_BIN] + FIXED_ARGS + DEFAULT_TUNING_ARGS, None
    try:
        tuning = shlex.split(text)
    except ValueError as e:
        return None, "SPOOFDPI_ARGS cannot be parsed: %s" % e
    for arg in tuning:
        name = arg.lstrip("-").split("=", 1)[0]
        if arg.startswith("-") and name in FORBIDDEN_FLAGS:
            return None, "SPOOFDPI_ARGS must not contain %s" % arg.split("=", 1)[0]
    return [SPOOFDPI_BIN] + FIXED_ARGS + tuning, None


def last_log_line():
    try:
        with open(LOG_PATH, "rb") as f:
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        lines = []
    non_empty = [line.strip() for line in lines if line.strip()]
    return non_empty[-1] if non_empty else "no log output"


def wait_listening(child):
    deadline = time.monotonic() + START_TIMEOUT
    while child.poll() is None and time.monotonic() < deadline:
        if port_accepts():
            return True
        time.sleep(POLL_INTERVAL)
    return False


class Host:
    def __init__(self, out):
        self._out = out
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._child = None

    def serve(self, fd):
        commands = {"start": self._start, "stop": self._stop, "status": self._status}
        while True:
            message = read_message(fd)
            if message is None:
                return
            cmd = message.get("cmd") if isinstance(message, dict) else None
            handler = commands.get(cmd) if isinstance(cmd, str) else None
            if handler is None:
                self._send(fail("unknown command"))
            else:
                handler()

    def stop_child(self):
        with self._state_lock:
            child, self._child = self._child, None
        if child is None:
            return
        terminate(child.send_signal, lambda: child.poll() is not None)
        child.wait()
        remove_pid_file()

    def _send(self, message):
        data = json.dumps(message).encode("utf-8")
        with self._write_lock:
            try:
                self._out.write(struct.pack("=I", len(data)) + data)
                self._out.flush()
            except OSError:
                pass  # Firefox closed the pipe; the reader sees EOF and shuts down.

    def _start(self):
        if self._child is not None and self._child.poll() is None:
            return self._send(OK)
        argv, error = spoofdpi_argv()
        error = error or precheck_error()
        if error:
            return self._send(fail(error))
        child = self._spawn(argv)
        # Re-check right before ok:true so a dead child can never be reported as started.
        if not wait_listening(child) or child.poll() is not None:
            self.stop_child()
            return self._send(fail("spoofdpi did not start: %s" % last_log_line()))
        self._send(OK)
        # Started only after the reply, so the extension always sees ok:true before any exited event.
        threading.Thread(target=self._monitor, args=(child,), daemon=True).start()

    def _spawn(self, argv):
        for path in (LOG_PATH, PID_PATH):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(LOG_PATH, "wb") as log:
            child = subprocess.Popen(
                argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        with self._state_lock:
            self._child = child
        with open(PID_PATH, "w") as f:
            f.write(str(child.pid))
        return child

    def _monitor(self, child):
        code = child.wait()
        with self._state_lock:
            if self._child is not child:
                return  # stopped on purpose or replaced
            self._child = None
            remove_pid_file()
        self._send({"event": "exited", "code": code})

    def _stop(self):
        self.stop_child()
        self._send(OK)

    def _status(self):
        child = self._child
        self._send({"ok": True, "running": child is not None and child.poll() is None})


def exit_on_signal(signum, frame):
    raise SystemExit(0)


def main():
    host = Host(sys.stdout.buffer)
    for sig in EXIT_SIGNALS:
        signal.signal(sig, exit_on_signal)
    try:
        host.serve(sys.stdin.fileno())
    finally:
        for sig in EXIT_SIGNALS:
            signal.signal(sig, signal.SIG_IGN)
        host.stop_child()
    return 0


if __name__ == "__main__":
    sys.exit(main())
