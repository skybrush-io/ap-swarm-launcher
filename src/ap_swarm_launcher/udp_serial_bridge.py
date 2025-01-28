from __future__ import annotations

from contextlib import asynccontextmanager, closing
from typing import AsyncIterator, TYPE_CHECKING

from trio import open_memory_channel, open_nursery, to_thread
from trio.abc import SendChannel
from trio.socket import (
    socket,
    AF_INET,
    IPPROTO_UDP,
    SOCK_DGRAM,
)

if TYPE_CHECKING:
    from serial import Serial

__all__ = ("UDPSerialBridge",)


class UDPSerialBridge:
    """Background task that creates a transparent bridge between a UDP port
    and a serial port.

    Packets received on the UDP port are serialized and forwarded to the serial
    port. Packets written to the serial port are forwarded to all UDP
    hostname-port pairs that have ever sent a packet to the UDP port.
    """

    _address: str
    _port: Serial
    _targets: set[tuple[str, int]]

    def __init__(self, address: str, port: Serial):
        """Constructor.

        Args:
            address: IP address and port to listen to
            port: serial port to forward the packets to
        """
        self._address = address
        self._port = port
        self._targets = set()

    @asynccontextmanager
    async def use(self) -> AsyncIterator[None]:
        host, _, port = self._address.partition(":")

        listener = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
        await listener.bind((host, port))

        async with open_nursery() as nursery:
            socket_tx = await nursery.start(self._write_to_socket, listener)
            port_tx = await nursery.start(self._write_to_serial_port)

            await nursery.start(self._read_from_socket, listener, port_tx)
            await nursery.start(self._read_from_serial_port, socket_tx)

            yield

    async def _read_from_serial_port(
        self, tx: SendChannel[bytes], *, task_status
    ) -> None:
        parts: list[bytes] = []
        bytes_read: int = 0

        MAX_BYTES: int = 4096

        async with tx:
            task_status.started()
            while True:
                data = await to_thread.run_sync(
                    self._port.read, 1, abandon_on_cancel=True
                )
                parts.append(data)
                bytes_read += len(data)

                while self._port.in_waiting > 0:
                    to_read = min(self._port.in_waiting, MAX_BYTES - bytes_read)
                    if to_read <= 0:
                        break

                    data = await to_thread.run_sync(
                        self._port.read, to_read, abandon_on_cancel=True
                    )
                    parts.append(data)
                    bytes_read += len(data)

                await tx.send(b"".join(parts))

                parts.clear()
                bytes_read = 0

    async def _read_from_socket(
        self, listener, tx: SendChannel[bytes], *, task_status
    ) -> None:
        with closing(listener):
            async with tx:
                task_status.started()
                while True:
                    data, address = await listener.recvfrom(4096)
                    self._targets.add(address)
                    await tx.send(data)

    async def _write_to_serial_port(self, *, task_status) -> None:
        tx, rx = open_memory_channel(32)

        async with rx:
            task_status.started(tx)
            async for data in rx:
                await to_thread.run_sync(self._port.write, data, abandon_on_cancel=True)

    async def _write_to_socket(self, socket, *, task_status) -> None:
        tx, rx = open_memory_channel(32)

        async with rx:
            task_status.started(tx)
            async for data in rx:
                # TODO(ntamas): maybe we should write to a multicast address
                # instead?
                for address in self._targets:
                    await socket.sendto(data, address)
