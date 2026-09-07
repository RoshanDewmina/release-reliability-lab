from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest


@pytest.fixture
def free_ports() -> Iterator[tuple[int, int]]:
    sockets = []
    ports = []
    for _ in range(2):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sockets.append(sock)
        ports.append(sock.getsockname()[1])
    for sock in sockets:
        sock.close()
    yield ports[0], ports[1]
