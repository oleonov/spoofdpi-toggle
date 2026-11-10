import json
import os
import queue
import shlex
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
HOST_SCRIPT = os.path.join(os.path.dirname(TESTS_DIR), "host", "spoofdpi_toggle_host.py")
FAKE_SCRIPT = os.path.join(TESTS_DIR, "fake_spoofdpi.py")
RECV_TIMEOUT = 8.0


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_accepts(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
        return True
    except OSError:
        return False


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def wait_until(condition, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def spawn_detached(path, *args):
    """Start a process reparented to launchd, like a child left behind by a dead host."""
    command = " ".join(shlex.quote(a) for a in (path,) + args) + " >/dev/null 2>&1 & echo $!"
    out = subprocess.run(["/bin/sh", "-c", command], stdout=subprocess.PIPE, check=True).stdout
    return int(out.decode().strip())


class HostClient:
    def __init__(self, env):
        self.proc = subprocess.Popen(
            [sys.executable, "-I", HOST_SCRIPT], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        self.messages = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        while True:
            header = self.proc.stdout.read(4)
            if len(header) < 4:
                return
            body = self.proc.stdout.read(struct.unpack("=I", header)[0])
            self.messages.put(json.loads(body.decode("utf-8")))

    def send(self, message):
        data = json.dumps(message).encode("utf-8")
        self.proc.stdin.write(struct.pack("=I", len(data)) + data)
        self.proc.stdin.flush()

    def recv(self, timeout=RECV_TIMEOUT):
        return self.messages.get(timeout=timeout)

    def request(self, cmd):
        self.send({"cmd": cmd})
        return self.recv()

    def assert_silent(self, test, seconds=0.7):
        try:
            message = self.recv(timeout=seconds)
        except queue.Empty:
            return
        test.fail("unexpected message %r" % message)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(timeout=5)
        self.proc.stdin.close()
        self.proc.stdout.close()


class HostTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="spoofdpi-toggle-test-")
        self.port = free_port()
        self.bin = os.path.join(self.tmp, "bin", "spoofdpi")
        self.argv_file = os.path.join(self.tmp, "argv.json")
        self.pid_file = os.path.join(self.tmp, "cache", "spoofdpi.pid")
        self.state_dir = os.path.join(self.tmp, "state")
        os.makedirs(os.path.dirname(self.bin))
        os.makedirs(self.state_dir)
        with open(self.bin, "w") as f:
            f.write("#!/bin/sh\nexec %s -I %s \"$@\"\n" % (shlex.quote(sys.executable), shlex.quote(FAKE_SCRIPT)))
        os.chmod(self.bin, 0o755)
        self.clients = []
        self.extra_pids = []

    def tearDown(self):
        for client in self.clients:
            client.close()
        for pid in self.extra_pids + [self.child_pid()]:
            if pid and pid_alive(pid):
                os.kill(pid, signal.SIGKILL)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, mode="listen", **overrides):
        env = {
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": self.tmp,
            "SPOOFDPI_BIN": self.bin,
            "SPOOFDPI_TOGGLE_PORT": str(self.port),
            "SPOOFDPI_TOGGLE_STATE_GLOB": os.path.join(self.state_dir, "spoofdpi*.state"),
            "SPOOFDPI_TOGGLE_LOG": os.path.join(self.tmp, "logs", "spoofdpi-toggle.log"),
            "SPOOFDPI_TOGGLE_PIDFILE": self.pid_file,
            "FAKE_SPOOFDPI_MODE": mode,
            "FAKE_SPOOFDPI_ARGV_FILE": self.argv_file,
        }
        env.update(overrides)
        return env

    def host(self, mode="listen", **overrides):
        client = HostClient(self.env(mode, **overrides))
        self.clients.append(client)
        return client

    def child_pid(self):
        try:
            with open(self.pid_file) as f:
                return int(f.read())
        except (OSError, ValueError):
            return None

    def started_child_pid(self, client):
        self.assertEqual({"ok": True}, client.request("start"))
        pid = self.child_pid()
        self.assertTrue(pid and pid_alive(pid))
        return pid

    def assert_not_spawned(self):
        self.assertFalse(os.path.exists(self.argv_file), "spoofdpi must not be spawned")

    def assert_rejected(self, reply, *fragments):
        self.assertFalse(reply["ok"])
        for fragment in fragments:
            self.assertIn(fragment, reply["error"])


class StartTest(HostTestCase):
    def test_start_listening_child_repliesOkWithConstantCleanArgv(self):
        client = self.host()
        self.started_child_pid(client)
        self.assertTrue(port_accepts(self.port))
        with open(self.argv_file) as f:
            argv = json.load(f)
        self.assertEqual([
            "--clean", "--no-tui", "--listen-addr", "127.0.0.1:%d" % self.port,
            "--dns-mode", "https", "--dns-https-url", "https://cloudflare-dns.com/dns-query",
            "--https-split-mode", "chunk", "--https-chunk-size", "1",
        ], argv)
        self.assertEqual({"ok": True, "running": True}, client.request("status"))
        self.assertEqual({"ok": True}, client.request("start"))

    def test_start_customArgs_replaceTuningAndKeepFixedPrefix(self):
        client = self.host(SPOOFDPI_ARGS="--https-split-mode sni --dns-https-url 'https://dns.example/q'")
        self.started_child_pid(client)
        with open(self.argv_file) as f:
            argv = json.load(f)
        self.assertEqual([
            "--clean", "--no-tui", "--listen-addr", "127.0.0.1:%d" % self.port,
            "--https-split-mode", "sni", "--dns-https-url", "https://dns.example/q",
        ], argv)

    def test_start_customArgsWithForbiddenFlag_repliesErrorWithoutSpawning(self):
        for args, flag in [
            ("--auto-configure-network", "--auto-configure-network"),
            ("-auto-configure-network=true", "-auto-configure-network"),
            ("--config=/tmp/x.toml", "--config"),
            ("-c /tmp/x.toml", "-c"),
            ("--clean=false", "--clean"),
            ("--listen-addr 0.0.0.0:1", "--listen-addr"),
            ("--app-mode socks5", "--app-mode"),
        ]:
            with self.subTest(args=args):
                client = self.host(SPOOFDPI_ARGS=args)
                self.assert_rejected(client.request("start"), "SPOOFDPI_ARGS must not contain " + flag)
                self.assert_not_spawned()

    def test_start_customArgsUnparseable_repliesErrorWithoutSpawning(self):
        client = self.host(SPOOFDPI_ARGS="--dns-https-url 'unterminated")
        self.assert_rejected(client.request("start"), "SPOOFDPI_ARGS cannot be parsed")
        self.assert_not_spawned()

    def test_start_binaryExitsImmediately_repliesErrorWithLastLogLine(self):
        client = self.host(mode="exit")
        self.assert_rejected(client.request("start"), "spoofdpi did not start", "fake spoofdpi: bad flag")
        client.assert_silent(self)
        self.assertEqual({"ok": True, "running": False}, client.request("status"))

    def test_start_childNeverListens_repliesErrorAndKillsChild(self):
        client = self.host(mode="silent")
        client.send({"cmd": "start"})
        pid = None
        if wait_until(lambda: self.child_pid() is not None):
            pid = self.child_pid()
        self.assert_rejected(client.recv(), "spoofdpi did not start")
        self.assertIsNotNone(pid)
        self.assertFalse(pid_alive(pid))

    def test_start_portBusy_repliesErrorWithoutSpawning(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", self.port))
            busy.listen(1)
            client = self.host()
            self.assert_rejected(client.request("start"), "port %d is busy" % self.port)
        self.assert_not_spawned()

    def test_start_binaryMissing_repliesNotFound(self):
        missing = os.path.join(self.tmp, "nope", "spoofdpi")
        client = self.host(SPOOFDPI_BIN=missing)
        self.assert_rejected(client.request("start"), "spoofdpi not found at %s" % missing)

    def test_start_networkStateFilePresent_repliesErrorWithoutSpawning(self):
        state_file = os.path.join(self.state_dir, "spoofdpi.http.darwin.state")
        open(state_file, "w").close()
        client = self.host()
        self.assert_rejected(client.request("start"), state_file, "delete the file")
        self.assert_not_spawned()
        self.assertTrue(os.path.exists(state_file))

    def test_start_stalePidOfSpoofdpi_killsStaleProcess(self):
        stale_bin = os.path.join(self.tmp, "stale", "spoofdpi")
        os.makedirs(os.path.dirname(stale_bin))
        os.symlink("/bin/sleep", stale_bin)
        stale = spawn_detached(stale_bin, "30")
        self.extra_pids.append(stale)
        os.makedirs(os.path.dirname(self.pid_file))
        with open(self.pid_file, "w") as f:
            f.write(str(stale))
        client = self.host()
        child = self.started_child_pid(client)
        self.assertTrue(wait_until(lambda: not pid_alive(stale)))
        self.assertNotEqual(stale, child)

    def test_start_stalePidOfOtherProcess_leavesItRunning(self):
        other = spawn_detached("/bin/sleep", "30")
        self.extra_pids.append(other)
        os.makedirs(os.path.dirname(self.pid_file))
        with open(self.pid_file, "w") as f:
            f.write(str(other))
        client = self.host()
        self.started_child_pid(client)
        self.assertTrue(pid_alive(other))


class StopAndLifecycleTest(HostTestCase):
    def test_stop_runningChild_killsItWithoutExitedEvent(self):
        client = self.host()
        pid = self.started_child_pid(client)
        self.assertEqual({"ok": True}, client.request("stop"))
        self.assertTrue(wait_until(lambda: not pid_alive(pid)))
        self.assertFalse(port_accepts(self.port))
        self.assertIsNone(self.child_pid())
        client.assert_silent(self)

    def test_stop_nothingRunning_repliesOk(self):
        client = self.host()
        self.assertEqual({"ok": True}, client.request("stop"))

    def test_stdinEof_killsChildAndExitsZero(self):
        client = self.host()
        pid = self.started_child_pid(client)
        client.proc.stdin.close()
        self.assertEqual(0, client.proc.wait(timeout=8))
        self.assertTrue(wait_until(lambda: not pid_alive(pid)))

    def test_exitSignals_killChildAndExitZero(self):
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            with self.subTest(signal=sig):
                client = self.host()
                pid = self.started_child_pid(client)
                client.proc.send_signal(sig)
                self.assertEqual(0, client.proc.wait(timeout=8))
                self.assertTrue(wait_until(lambda: not pid_alive(pid)))

    def test_unknownCommand_repliesErrorAndKeepsServing(self):
        client = self.host()
        self.assert_rejected(client.request("rm -rf /"), "unknown command")
        client.send({"no": "cmd"})
        self.assert_rejected(client.recv(), "unknown command")
        self.assertEqual({"ok": True, "running": False}, client.request("status"))
        self.assert_not_spawned()


class ExitedEventTest(HostTestCase):
    def test_childCrashAfterStart_emitsExitedAfterOk(self):
        client = self.host(mode="crash_after_listen")
        self.assertEqual({"ok": True}, client.request("start"))
        self.assertEqual({"event": "exited", "code": 3}, client.recv())
        self.assertEqual({"ok": True, "running": False}, client.request("status"))

    def test_childDiesOnFirstProbe_okAlwaysPrecedesExited(self):
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                client = self.host(mode="die_on_probe")
                reply = client.request("start")
                self.assertIn("ok", reply)
                if reply["ok"]:
                    self.assertEqual({"event": "exited", "code": 7}, client.recv())
                else:
                    self.assertIn("spoofdpi did not start", reply["error"])
                    client.assert_silent(self)


if __name__ == "__main__":
    unittest.main()
