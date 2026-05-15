"""Shared test fixtures and the FakeTransport used by every non-pure test.

`FakeTransport` is a scripted transport: you give it a list of
`(expected_frame, reply_bytes)` pairs in the exact order the test expects,
and it asserts on mismatch. This keeps tests strict about *what bytes go on
the wire* — drift in the protocol layer becomes a test failure, not a
silent regression.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sy01b.errors import TransportClosed
from sy01b.protocol import ETX


def dt_reply(status_byte: int, data: bytes = b"") -> bytes:
    """Helper: assemble a DT-shaped reply frame.

    The pump always echoes b'0' as the master address (per
    [SY01BE.pdf](../SY01BE.pdf) §4.2.2), so the reply header is fixed.
    """
    return b"/0" + bytes([status_byte]) + data + ETX + b"\r\n"


@dataclass
class FakeTransport:
    scripted: list[tuple[bytes, bytes]] = field(default_factory=list)
    sent: list[bytes] = field(default_factory=list)
    closed: bool = False

    def send(self, frame: bytes, deadline_s: float) -> bytes:
        if self.closed:
            raise TransportClosed("FakeTransport is closed")
        if not self.scripted:
            raise AssertionError(f"FakeTransport received {frame!r} but no replies are scripted")
        expected, reply = self.scripted.pop(0)
        if frame != expected:
            raise AssertionError(f"FakeTransport expected {expected!r}, got {frame!r}")
        self.sent.append(frame)
        return reply

    def close(self) -> None:
        self.closed = True

    def expect(self, frame: bytes, reply: bytes) -> None:
        """Convenience: append one (frame, reply) pair to the script."""
        self.scripted.append((frame, reply))

    def all_consumed(self) -> bool:
        return not self.scripted
