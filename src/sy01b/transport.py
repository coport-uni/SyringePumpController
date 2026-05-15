"""Serial transport for the DT protocol.

Defines a `Transport` Protocol (duck-typed interface) and one concrete
implementation `DTTransport` backed by pyserial. A fake implementation
lives in tests/conftest.py.

Read loop terminates on ETX or wall-clock deadline. The transport never
retries — DT has no sequence number, so duplicate writes can re-trigger
moves. Retransmission is the caller's call.
"""

from __future__ import annotations

import time
from typing import Protocol

import serial

from sy01b._logging import hex_preview, logger
from sy01b.config import PumpConfig
from sy01b.errors import TransportClosed, TransportTimeout
from sy01b.protocol import ETX


class Transport(Protocol):
    def send(self, frame: bytes, deadline_s: float) -> bytes: ...
    def close(self) -> None: ...


class DTTransport:
    """Real pyserial transport. Opens the port with CH340-friendly flags."""

    def __init__(self, serial_port: serial.Serial) -> None:
        self._serial = serial_port

    @classmethod
    def open(cls, cfg: PumpConfig) -> DTTransport:
        port = serial.Serial(
            port=cfg.port,
            baudrate=cfg.baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=cfg.reply_timeout_s,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        port.dtr = False
        port.rts = False
        logger.debug("opened %s @ %d 8N1 (DTR/RTS forced low)", cfg.port, cfg.baud)
        return cls(port)

    def send(self, frame: bytes, deadline_s: float) -> bytes:
        if not self._serial.is_open:
            raise TransportClosed("serial port is not open")
        logger.debug("→ %s", hex_preview(frame))
        self._serial.reset_input_buffer()
        self._serial.write(frame)
        self._serial.flush()
        buf = bytearray()
        start = time.monotonic()
        while True:
            chunk = self._serial.read(64)
            if chunk:
                buf.extend(chunk)
                if ETX in buf:
                    # consume trailing CR/LF if present so the next read starts clean
                    end = buf.index(ETX) + 1
                    while end < len(buf) and buf[end] in (0x0D, 0x0A):
                        end += 1
                    # CH340 dongles occasionally emit a stray byte (0xFF, NUL, etc.) before
                    # the start-of-frame on the first reply after open. The frame itself
                    # starts at the first '/'; drop anything before it.
                    start = buf.find(b"/")
                    reply = bytes(buf[start:end]) if 0 <= start < end else bytes(buf[:end])
                    logger.debug("← %s", hex_preview(reply))
                    return reply
            if time.monotonic() - start > deadline_s:
                raise TransportTimeout(
                    elapsed_s=time.monotonic() - start,
                    frame_sent=frame,
                    partial=bytes(buf),
                )

    def close(self) -> None:
        if self._serial.is_open:
            self._serial.close()
            logger.debug("serial port closed")
