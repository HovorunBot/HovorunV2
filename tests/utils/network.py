"""Network test utilities for preventing or stubbing real external socket connections."""

import socket

ORIGINAL_CONNECT = socket.socket.connect


def reject_network(connection: socket.socket, address: object) -> None:
    """Reject any attempted network connection in offline administration tests."""
    if isinstance(address, tuple) and address[1] in {5432, 15432}:
        ORIGINAL_CONNECT(connection, address)
        return
    msg = "Offline administration attempted an external network connection."
    raise AssertionError(msg)
