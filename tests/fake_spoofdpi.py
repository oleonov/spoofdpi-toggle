"""Stand-in for the spoofdpi binary; behaviour is chosen by FAKE_SPOOFDPI_MODE."""
import json
import os
import socket
import sys
import time

MAX_LIFETIME = 30  # a leaked fake must not outlive the test run for long


def listen_socket():
    host, port = sys.argv[sys.argv.index("--listen-addr") + 1].rsplit(":", 1)
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((host, int(port)))
    server.listen(16)
    return server


def serve_forever(server):
    server.settimeout(0.5)
    deadline = time.monotonic() + MAX_LIFETIME
    while time.monotonic() < deadline:
        try:
            server.accept()[0].close()
        except socket.timeout:
            pass


def main():
    with open(os.environ["FAKE_SPOOFDPI_ARGV_FILE"], "w") as f:
        json.dump(sys.argv[1:], f)
    mode = os.environ.get("FAKE_SPOOFDPI_MODE", "listen")
    if mode == "exit":
        sys.stderr.write("fake spoofdpi: bad flag\n")
        return 2
    if mode == "silent":
        time.sleep(MAX_LIFETIME)
        return 0
    server = listen_socket()
    if mode == "crash_after_listen":
        time.sleep(0.5)
        return 3
    if mode == "die_on_probe":
        server.accept()
        return 7
    serve_forever(server)
    return 0


if __name__ == "__main__":
    sys.exit(main())
