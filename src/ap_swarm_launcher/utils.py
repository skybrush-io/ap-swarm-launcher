from contextlib import AbstractContextManager, AsyncExitStack, contextmanager, ExitStack
from os import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from trio import open_file, sleep_forever
from trio.socket import (
    socket,
    AF_INET,
    IPPROTO_IP,
    IPPROTO_UDP,
    IP_MULTICAST_TTL,
    SOCK_DGRAM,
)

from typing import Iterator, Optional, Union

__all__ = (
    "copy_file_async",
    "route_local_broadcast_traffic_to_multicast",
)


async def copy_file_async(src, dest) -> None:
    """Copies a source file to a destination path asynchronously.

    Both the source and the destination may be existing async file handlers,
    filenames or Path objects.
    """
    chunk_size = 65536
    stack = AsyncExitStack()
    async with stack:
        if not hasattr(src, "read"):
            in_fp = await stack.enter_async_context(await open_file(src, mode="rb"))
        else:
            in_fp = src

        if not hasattr(dest, "write"):
            if dest.is_dir():
                dest = dest / src.name
            out_fp = await stack.enter_async_context(await open_file(dest, mode="wb+"))
        else:
            out_fp = dest

        while True:
            data = await in_fp.read(chunk_size)
            if not data:
                break

            await out_fp.write(data)


async def route_local_broadcast_traffic_to_multicast(
    address: Optional[str], local_port: int = 14555
):
    """Asynchronous task that forwards UDP packets sent to a port on
    localhost to the given multicast address.

    Parameters:
        address: the multicast address to re-route the local broadcast traffic
            to, specified as HOST:PORT
        port: the port to listen on for local broadcast traffic
    """
    group, _, port = (address or "").partition(":")
    if not port:
        # Invalid or missing multicast address, nothing to do
        await sleep_forever()
        return

    listener = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
    await listener.bind(("127.0.0.1", local_port))

    sender = socket(AF_INET, SOCK_DGRAM, IPPROTO_UDP)
    sender.setsockopt(IPPROTO_IP, IP_MULTICAST_TTL, 2)
    multicast_address = (group, int(port))

    try:
        while True:
            data, address = await listener.recvfrom(4096)
            await sender.sendto(data, multicast_address)

    finally:
        listener.close()
        sender.close()


def temporary_directory(**kwds) -> AbstractContextManager:
    """Returns a context manager that creates a temporary directory."""
    return TemporaryDirectory(prefix="sitl-swarm-", **kwds)


@contextmanager
def temporary_working_directory(**kwds) -> Iterator[Path]:
    """Returns a context manager that creates a temporary directory and changes
    the current directory to it.
    """
    with temporary_directory(**kwds) as tmpdir:
        with working_directory(tmpdir) as result:
            yield result


@contextmanager
def working_directory(path: Union[str, Path]) -> Iterator[Path]:
    """Context manager that changes the working directory to the given path when
    the context is entered and restores it when it is exited.
    """
    cwd = Path.cwd()
    path = Path(path)
    try:
        chdir(path)
        yield path
    finally:
        chdir(cwd)


@contextmanager
def maybe_temporary_working_directory(
    path: Optional[Union[str, Path]] = None, **kwds
) -> Iterator[Path]:
    """Context manager that enters the given directory as working directory,
    unless it is missing (`None`), in which case it creates a new temporary
    working directory and enters that.
    """
    with ExitStack() as stack:
        if not path:
            path = stack.enter_context(temporary_working_directory(**kwds))
        yield Path(path).resolve()
