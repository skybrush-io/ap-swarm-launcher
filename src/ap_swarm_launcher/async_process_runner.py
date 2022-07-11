"""Asyncronous Process runner object that can manage and monitor multiple
Python subprocess.Popen_ instances and display their stdout and stderr in a
separate async task.
"""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from signal import Signals
from subprocess import PIPE, STDOUT
from sys import exc_info
from trio import (
    Event,
    open_memory_channel,
    open_nursery,
    Process,
    sleep,
    TASK_STATUS_IGNORED,
)
from trio.abc import ReceiveStream, SendStream, SendChannel
from typing import Any, Callable, List, Optional, Union

try:
    from trio.lowlevel import open_process
except ImportError:
    from trio import open_process  # type: ignore

__all__ = ("AsyncProcessRunner", "AsyncProcessRunnerContext")


@dataclass
class ManageProcessRequest:
    name: Optional[str]
    args: Any
    kwds: Any
    daemon: bool = False
    stream_stdout: bool = True
    use_stdin: bool = False

    on_started: Optional[Callable[["ManagedAsyncProcess"], None]] = None


@dataclass
class ManagedAsyncProcess:
    daemon: bool = False
    name: Optional[str] = None
    process: Optional[Process] = None
    termination_attempts: int = 0
    write: Optional[Callable[[Union[bytes, str]], None]] = None

    @property
    def stderr(self) -> Optional[ReceiveStream]:
        return self.process.stderr if self.process else None

    @property
    def stdin(self) -> Optional[SendStream]:
        return self.process.stdin if self.process else None

    @property
    def stdout(self) -> Optional[ReceiveStream]:
        return self.process.stdout if self.process else None

    def terminate(self) -> None:
        """Attempts to terminate the process."""
        if self.process is None:
            return

        self.termination_attempts += 1
        if self.termination_attempts <= 5:
            self.process.terminate()
        else:
            self.process.kill()

    @property
    def terminated(self) -> bool:
        return self.process is None or self.process.returncode is not None

    async def wait(self) -> int:
        if not self.process:
            raise RuntimeError("process is not running")
        return await self.process.wait()


@dataclass
class PrintRequest:
    """Command instance that is placed in the command queue of the manager
    thread when one of the worker threads or the main thread wants to print
    something.
    """

    line: str
    process: Optional[ManagedAsyncProcess] = None
    meta: bool = False


class _LineReader:
    def __init__(self, stream: ReceiveStream, max_line_length: int = 16384):
        self.stream = stream
        self._line_generator = self.generate_lines(max_line_length)

    @staticmethod
    def generate_lines(max_line_length: int):
        buf = bytearray()
        find_start = 0
        while True:
            newline_idx = buf.find(b"\n", find_start)
            if newline_idx < 0:
                # next time, start the search where this one left off
                find_start = len(buf)
                more_data = yield
            else:
                # b'\n' found in buf so return the line and move up buf
                line = buf[: newline_idx + 1]
                # Update the buffer in place, to take advantage of bytearray's
                # optimized delete-from-beginning feature.
                del buf[: newline_idx + 1]
                # next time, start the search from the beginning
                find_start = 0
                more_data = yield line

            if more_data is not None:
                buf += bytes(more_data)

    async def readline(self):
        line = next(self._line_generator)
        while line is None:
            more_data = await self.stream.receive_some(1024)
            if not more_data:
                return None

            line = self._line_generator.send(more_data)
        return line


class AsyncProcessRunnerContext:
    """Process runner object that can manage and monitor multiple Trio
    Process_ instances and display their stdout and stderr in a separate
    async task.
    """

    _owner: "_AsyncProcessRunner"

    def __init__(self, owner: "_AsyncProcessRunner", queue: SendChannel):
        self._command_queue = queue
        self._manager_task = None
        self._exiting = False
        self._owner = owner

    @property
    def exiting(self) -> bool:
        return self._exiting

    async def mark(self) -> None:
        """Adds a marker to the merged output streams of the processes."""
        await self.write("--- mark ---")

    def register_termination_handler(
        self, func: Callable[[ManagedAsyncProcess, int], None]
    ) -> None:
        return self._owner.register_termination_handler(func)

    def request_stop(self) -> None:
        self._exiting = True

    async def start(self, *args, **kwds) -> ManagedAsyncProcess:
        """Executes a process and starts managing its standard output and
        error stream.

        Blocks until the process has been started.

        All arguments not mentioned here are passed on intact to `open_process()`_.

        Parameters:
            daemon: whether the process will be a daemon process. The runner
                will terminate when all non-daemon processes have exited on
                their own. Any daemon processes still running are then
                terminated automatically.
            name: the name of the process; this will be used to identify the
                process when the stdouts of multiple processes are merged in
                the stdout of the process runner
            stream_stdout: whether to stream the standard output of the spawned
                process to the common, merged stdout. Defaults to `True`; pass
                `False` here if you want to read the stdout on your own.
            use_stdin: whether you want to use the standard input of the spawned
                process. Defaults to `False`, which will close the `stdin` of
                the process immediately.

        Returns:
            a ManagedAsyncProcess object providing limited access to the spawned
            process
        """
        if self.exiting:
            raise RuntimeError("process runner is already shutting down")

        event = Event()
        result = []

        daemon = bool(kwds.pop("daemon", False))
        name = kwds.pop("name", None)
        stream_stdout = kwds.pop("stream_stdout", True)
        use_stdin = kwds.pop("use_stdin", False)

        def on_started(process):
            result.append(process)
            event.set()

        request = ManageProcessRequest(
            name=name,
            args=args,
            kwds=kwds,
            daemon=daemon,
            stream_stdout=bool(stream_stdout),
            use_stdin=bool(use_stdin),
            on_started=on_started,
        )

        await self._command_queue.send(request)
        await event.wait()
        return result.pop()

    async def write(self, line: str) -> None:
        """Writes an arbitrary line to the merged output streams of the
        processes. May be used only when the process runner is running.
        """
        await self._command_queue.send(PrintRequest(line))

    def write_nowait(self, line: str) -> None:
        """Writes an arbitrary line to the merged output streams of the
        processes. May be used only when the process runner is running.
        """
        try:
            self._command_queue.send_nowait(PrintRequest(line))  # type: ignore
        except Exception:
            print(line)


class _AsyncProcessRunner:
    """Process runner object that can manage and monitor multiple Python
    subprocess.Popen_ instances and display their stdout and stderr in a
    separate thread.
    """

    _managed_processes: Optional[List[ManagedAsyncProcess]]
    _on_terminated: List[Callable[[ManagedAsyncProcess, int], None]]

    def __init__(self, sidebar_width: int = 10):
        """Constructor.

        Parameters:
            sidebar_width: width of the sidebar where the name of the process
                is shown in the merged process output
        """
        self._command_queue_tx, self._command_queue_rx = None, None
        self._managed_processes = None
        self._nursery = None
        self._on_terminated = []

        self.sidebar_width = sidebar_width

    @asynccontextmanager
    async def _operate(self):
        if self._nursery is not None:
            raise ValueError("process runner already running")

        try:
            self._command_queue_tx, self._command_queue_rx = open_memory_channel(256)
            context = AsyncProcessRunnerContext(self, self._command_queue_tx)

            self._managed_processes = []

            async with open_nursery() as nursery:
                # The manager task runs in a separate nursery so the process
                # tasks get cleaned up earlier
                await nursery.start(
                    self._run_manager_task,
                    context.write,
                )
                async with open_nursery() as self._nursery:
                    try:
                        yield context
                    finally:
                        await self._cleanup()

        finally:
            self._command_queue_tx, self._command_queue_rx = None, None
            self._managed_processes = None
            self._nursery = None

    async def _cleanup(self):
        exc_type, _, _ = exc_info()

        processes = list(self._managed_processes or [])
        self._managed_processes = None

        if not exc_type:
            # First we wait until all the non-daemon processes terminate
            try:
                while processes:
                    for process in processes:
                        if not process.daemon:
                            await process.wait()
                            processes = [
                                entry for entry in processes if not entry.terminated
                            ]
                            break
                    else:
                        # No non-daemon process
                        break
            except KeyboardInterrupt:
                pass

        # If we got an exception or we were interrupted by a KeyboardInterrupt,
        # terminate all processes and then resume waiting for them
        while processes:
            for entry in processes:
                entry.terminate()

            for i in range(5):
                processes = [entry for entry in processes if not entry.terminated]
                if not processes:
                    break

                await sleep(0.1)

        if self._command_queue_tx:
            await self._command_queue_tx.send(None)

    def _process_print_request(self, request: PrintRequest) -> None:
        self._write(request.process, request.line, request.meta)

    def _write(
        self,
        process: Optional[ManagedAsyncProcess],
        line: str,
        meta: bool = False,
    ) -> None:
        sidebar_width = self.sidebar_width

        if isinstance(line, (bytes, bytearray)):
            line = line.decode("utf-8", "replace")

        line = line.rstrip()

        name = (process.name if process else None) or ""
        if len(name) > sidebar_width:
            name = name[: (sidebar_width - 4)] + "…" + name[-3:]
        print(name.rjust(sidebar_width) + " | " + line)

    async def _run_manager_task(
        self, write, *, task_status=TASK_STATUS_IGNORED
    ) -> None:
        task_status.started()

        queue = self._command_queue_rx
        processes = self._managed_processes

        assert queue is not None
        assert processes is not None

        while True:
            command = await queue.receive()

            if command is None:
                break

            elif isinstance(command, ManageProcessRequest):
                entry = ManagedAsyncProcess()
                entry.write = partial(self._write, entry)  # type: ignore
                if self._nursery:
                    self._nursery.start_soon(self._run_worker_task, entry, command)
                entry.daemon = command.daemon
                processes.append(entry)

            elif isinstance(command, PrintRequest):
                self._process_print_request(command)

    async def _run_worker_task(self, entry, command) -> None:
        process = await open_process(
            *command.args,
            **command.kwds,
            stdin=PIPE if command.use_stdin else None,
            stdout=PIPE,
            stderr=STDOUT if command.stream_stdout else PIPE,
        )

        entry.process = process
        entry.name = (
            f"Process #{process.pid}" if command.name is None else str(command.name)
        )

        command.on_started(entry)

        write = entry.write

        if command.stream_stdout:
            assert process.stdout is not None
            reader = _LineReader(process.stdout)
            while True:
                line = await reader.readline()
                if line is None:
                    break
                write(line)

        code = await process.wait()

        if code < 0:
            signal = Signals(-code).name
            write(
                f"Subprocess exited with signal {signal}",
                meta=True,
            )
        elif code > 0:
            write(
                f"Subprocess exited with code {code}",
                meta=True,
            )
        else:
            write("Subprocess terminated.", meta=True)

        self._notify_process_termination(entry, code, write)

    def register_termination_handler(
        self, func: Callable[[ManagedAsyncProcess, int], None]
    ) -> None:
        """Registers a function to be called when a process supervised by the
        process runner terminates.
        """
        self._on_terminated.append(func)

    def _notify_process_termination(
        self, process: ManagedAsyncProcess, code: int, write
    ) -> None:
        """Calls all registered process termination handlers when a process
        exited.
        """
        for handler in self._on_terminated:
            try:
                handler(process, code)
            except Exception:
                raise
                write(
                    f"Error while calling process termination handler {handler!r}",
                    meta=True,
                )


@asynccontextmanager
async def AsyncProcessRunner(*args, **kwds):
    """Creates an async process runner object that can manage and monitor
    multiple subprocesses and display their stdout and stderr streams,
    nicely merged in the stdout of the current process.

    This object is meant to be used as an async context manager. A manager
    task responsible for merging the stdout and stderr streams of the spawned
    processes is started when entering the context. After the context is
    exited, the manager task and all the tasks corresponding to the
    individual processes will be stopped.
    """
    runner = _AsyncProcessRunner(*args, **kwds)
    async with runner._operate() as context:
        yield context


async def _test():
    async with AsyncProcessRunner(sidebar_width=20) as runner:
        await runner.start("while true; do echo yes; sleep 0.5; done", shell=True)
        await runner.start("while true; do echo no; sleep 0.3; done", shell=True)


if __name__ == "__main__":
    import trio

    trio.run(_test)
