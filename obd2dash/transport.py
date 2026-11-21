"""Serial-like transports.

``serial.Serial`` (pyserial) is used for COM ports / /dev/tty* / /dev/rfcomm*.
``RfcommSocket`` talks to a *paired* Bluetooth ELM327 directly through a BlueZ RFCOMM socket (Linux),
so no ``rfcomm bind`` / root is needed. Both expose the small subset the driver needs:
``read(n)``, ``write(bytes)``, ``in_waiting``, ``close()``, ``is_open``.
"""
from __future__ import annotations

import select
import socket

from .i18n import LocalizedError, msg


class TransportError(LocalizedError):
    """The transport cannot be used on this system (message is shown to the user)."""


def bluetooth_sockets_available() -> bool:
    return hasattr(socket, "AF_BLUETOOTH") and hasattr(socket, "BTPROTO_RFCOMM")


class RfcommSocket:
    """pyserial-like wrapper around a connected stream socket."""

    def __init__(self, sock: socket.socket, timeout: float = 0.1):
        self._sock = sock
        self.timeout = timeout
        self._open = True

    @classmethod
    def connect(cls, mac: str, channel: int = 1, connect_timeout: float = 12.0) -> "RfcommSocket":
        if not bluetooth_sockets_available():
            raise TransportError(msg("err.no_bt_sockets"))
        s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)  # type: ignore[attr-defined]
        try:
            s.settimeout(connect_timeout)
            s.connect((mac, channel))
        except OSError:
            s.close()
            raise
        return cls(s)

    # --- pyserial-like API -------------------------------------------------
    @property
    def is_open(self) -> bool:
        return self._open

    def _check(self) -> None:
        if not self._open:
            raise OSError("socket closed")

    def write(self, data: bytes) -> int:
        self._check()
        self._sock.sendall(data)
        return len(data)

    @property
    def in_waiting(self) -> int:
        """Bytes ready to be read (0 if none). Raises OSError if the peer closed the link."""
        self._check()
        readable, _, _ = select.select([self._sock], [], [], 0)
        if not readable:
            return 0
        data = self._sock.recv(4096, socket.MSG_PEEK)
        if not data:
            raise OSError("connection closed by the adapter")
        return len(data)

    def read(self, size: int = 1) -> bytes:
        self._check()
        self._sock.settimeout(self.timeout)
        try:
            data = self._sock.recv(size)
        except socket.timeout:
            return b""
        if not data:
            raise OSError("connection closed by the adapter")
        return data

    def flush(self) -> None:  # pyserial compatibility
        pass

    def close(self) -> None:
        self._open = False
        try:
            self._sock.close()
        except OSError:
            pass
