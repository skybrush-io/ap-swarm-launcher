from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager, closing
from functools import lru_cache
from importlib.resources import path as resource_path
from itertools import count
from pathlib import Path
from trio import open_file, open_nursery, Path as AsyncPath
from typing import (
    AsyncIterator,
    ContextManager,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
    Union,
)

from .async_process_runner import (
    AsyncProcessRunner,
    AsyncProcessRunnerContext,
    ManagedAsyncProcess,
)
from .formations import Point
from .locations import (
    DEFAULT_LOCATION,
    FlatEarthToGPSCoordinateTransformation,
    GPSCoordinate,
)
from .udp_serial_bridge import UDPSerialBridge
from .utils import copy_file_async, maybe_temporary_working_directory

__all__ = (
    "SimulatedDroneSwarm",
    "SimulatedDroneSwarmContext",
)


_uart_id_to_index_map: dict[str, int] = {
    "A": 0,
    "B": 3,
    "C": 1,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 6,
    "H": 7,
    "I": 8,
    "J": 9,
}

"""Mapping from character-based UART IDs (uartA, uartB etc) to UART indices
used in the SITL from ArduCopter 4.5.0.
"""

_uart_index_to_id_map: dict[int, str] = {v: k for k, v in _uart_id_to_index_map.items()}
"""Mapping from UART indices to character-based UART IDs (uartA, uartB etc)
used in the SITL before ArduCopter 4.5.0.
"""


def _to_uart_index(id_or_index: Union[str, int]) -> int:
    """Takes an input that is either a numeric UART index or a character-based
    UART ID and returns the numeric UART index.
    """
    if isinstance(id_or_index, int):
        return id_or_index
    else:
        return _uart_id_to_index_map[id_or_index]


def _to_uart_id(id_or_index: Union[str, int]) -> str:
    """Takes an input that is either a numeric UART index or a character-based
    UART ID and returns the character-based UART ID.
    """
    if isinstance(id_or_index, str):
        return id_or_index
    else:
        return _uart_index_to_id_map[id_or_index]


def create_args_for_simulator(
    model: Optional[str] = None,
    param_file: Optional[Union[str, Path]] = None,
    use_console: bool = False,
    home: GPSCoordinate = DEFAULT_LOCATION.origin,
    heading: float = 0,
    index: Optional[int] = 0,
    uarts: Optional[Dict[str, str]] = None,
    rc_input_port: Optional[int] = None,
    speedup: float = 1,
) -> List[str]:
    """Creates the argument list to execute the SITL simulator.

    Parameters:
        model: dynamics model of the simulated vehicle
        param_file: name or path of the parameter file that holds the default
            parameters of the vehicle
        use_console: whether the SITL simulator should use the console instead
            of a TCP port for its primary input / output stream
        home: the home position of the drone
        heading: the initial heading of the drone
        index: specifies the index of the drone when multiple simulators are
            being run on the same computer; each increase in the index shifts
            all the ports used by the drone by +10
        uarts: dictionary mapping SITL UART identifiers (from A to H) to roles.
            See the SITL documentation for the possible roles. Examples are:
            `tcp:localhost:5760` creates a TCP server socket on port 5760;
            `udpclient:localhost:14550` creates a UDP socket that sends packets
            to port 14550.
        rc_input_port: port to listen on for RC input
        speedup: simulation speedup factor; use 1 for real-time
    """
    model = model or "quad"

    result = ["-M", model]

    if index is not None:
        if index < 0:
            raise ValueError("index must be non-negative")

        result.extend(["-I", str(index)])

    if use_console:
        result.append("-C")

    if param_file:
        result.extend(["--defaults", str(param_file)])

    if rc_input_port is not None:
        result.extend(["--rc-in-port", str(rc_input_port)])

    if uarts:
        for uart_id_or_index, value in uarts.items():
            uart_index = _to_uart_index(uart_id_or_index)
            result.append(f"--serial{uart_index}={value}")

    home_as_str = f"{home.lat:.7f},{home.lon:.7f},{home.amsl:.1f},{int(heading)}"
    result.extend(["--home", home_as_str])

    # Add --speedup argument unconditionally because there is a bug in the SITL
    # as of ArduCopter 4.2.1 that crashes the SITL on macOS if the speedup is
    # not set explicitly
    result.extend(["--speedup", str(speedup)])

    return result


@lru_cache(maxsize=None)
def get_simulator_version(executable: Path) -> tuple[int, int, int]:
    """Determines the version number of the simulator running at the given
    path.

    We cannot really determine the version number exactly, but we can look at
    the help string and decide whether we are pre-4.5.0 or post-4.5.0.
    """
    from subprocess import run

    proc = run([executable, "--help"], capture_output=True)
    if b"--disable-fgview" in proc.stdout:
        return (4, 4, 4)
    else:
        return (4, 5, 0)


async def start_simulator(
    executable: Path,
    *,
    runner: AsyncProcessRunnerContext,
    name: str = "ArduCopter",
    cwd: Optional[Union[str, Path]] = None,
    **kwds,
) -> ManagedAsyncProcess:
    """Starts the SITL simulator, supervised by the given asynchronous process
    runner.

    Keyword arguments not mentioned here are forwarded to
    `create_args_for_simulator()`.

    Keyword arguments:
        param_file: name or path of the parameter file that holds the default
            parameters of the
        runner: the process runner that will supervise the execution of the
            simulator
        name: the name of the spawned process in the process runner
        cwd: the current working directory of the ArduCopter process; this is
            the folder where the EEPROM contents will be read from / written to

    Returns:
        the ManagedProcess instance that represents the launched simulator
    """
    args: list[str]

    args = [str(executable)]
    args.extend(create_args_for_simulator(**kwds))

    # Fix up the simulator arguments for ArduCopter pre-4.5.0 where fgview
    # was enabled by default and had to be disabled explicitly
    if get_simulator_version(args[0]) < (4, 5, 0):

        def fixup_arg(arg: str) -> str:
            for i in range(10):
                if arg.startswith(f"--serial{i}="):
                    _, _, value = arg.partition("=")
                    name = "--uart" + _to_uart_id(i)
                    return f"{name}={value}"
            return arg

        args = [fixup_arg(arg) for arg in args]
        args.append("--disable-fgview")

    if cwd is not None:
        cwd = str(cwd)

    stream_stdout = "-C" not in args
    use_stdin = "-C" in args

    process = await runner.start(
        args,
        name=name,
        daemon=True,
        cwd=cwd,
        stream_stdout=stream_stdout,
        use_stdin=use_stdin,
    )

    return process


class SimulatedDroneSwarm:
    """Object that is responsible for managing a simulated drone swarm by
    spawing the appropriate ArduPilot SITL processes for each simulated
    drone.
    """

    _executable: Path
    """Full path to the ArduPilot SITL executable."""

    _gcs_address: Optional[str] = None

    _swarm_dir: Optional[Path] = None

    _serial_port: Optional[str] = None
    """Virtual serial port where the status packets of the simulated drones will be
    aggregated and dumped to. ``None`` if the serial port is not needed.
    """

    _tcp_base_port: Optional[int] = None
    """Base number of a TCP port range where the simulated instances will listen
    for incoming connections, or ``None`` if TCP ports are not needed. The i-th
    simulated instance (0-based indexing) will listen at ``_tcp_base_port + i``.
    """

    def __init__(
        self,
        executable: Path,
        dir: Optional[Path] = None,
        params: Optional[Sequence[Union[str, Path, Tuple[str, float]]]] = None,
        coordinate_system: Optional[FlatEarthToGPSCoordinateTransformation] = None,
        amsl: Optional[float] = None,
        default_heading: Optional[float] = None,
        gcs_address: Optional[str] = "127.0.0.1:14550",
        multicast_address: Optional[str] = None,
        tcp_base_port: Optional[int] = None,
        serial_port: Optional[str] = None,
        model: Optional[str] = None,
    ):
        """Constructor.

        Parameters:
            executable: full path to the ArduPilot SITL executable
            dir: the configuration directory of the swarm. When omitted, a
                temporary directory will be created for the content related
                to the swarm.
            coordinate_system: transformation that converts local coordinates
                to GPS coordinates. Defaults to a coordinate system derived
                from `DEFAULT_LOCATION`
            default_heading: the default heading of the drones in the swarm;
                `None` means to align it with the X axis of the local
                coordinate system (which is also the default)
            params: parameters to pass to the simulator; it must be a list where
                each entry is either a Path object pointing to a parameter file
                or a name-value pair as a tuple
            gcs_address: target IP address and port where the simulated drones
                will send their UDP status packets, or ``None`` or an empty string
                if the simulated drones do not need to send UDP status packets
            multicast_address: optional multicast IP address and port where the
                simulated drones will listen for packets that are intended to
                reach all the drones in the swarm
            tcp_base_port: TCP port number where the simulated drones will
                be available via a TCP connection. This is the base port
                number; each drone will get a new TCP port, counting upwards
                from this base port number.
            model: optional vehicle model name passed to the simulator
        """
        self._executable = Path(executable)
        self._dir = Path(dir) if dir else None
        self._params = list(params) if params else []
        self._serial_port = serial_port
        self._tcp_base_port = int(tcp_base_port) if tcp_base_port else None

        if coordinate_system:
            self._coordinate_system = coordinate_system
        else:
            self._coordinate_system = DEFAULT_LOCATION.coordinate_system

        self._default_heading = (
            float(default_heading) % 360
            if default_heading is not None
            else self._coordinate_system.orientation
        )

        self._gcs_address = gcs_address
        self._multicast_address = multicast_address
        self._model = model

        self._index_generator = count(1)

        self._nursery = None
        self._runner = None
        self._swarm_dir = None

    @asynccontextmanager
    async def use(self) -> AsyncIterator[SimulatedDroneSwarmContext]:
        """Async context manager that starts the swarm when entered and stops
        the swarm when exited.
        """
        async with AsyncExitStack() as stack:
            self._nursery = await stack.enter_async_context(open_nursery())
            self._swarm_dir = stack.enter_context(
                maybe_temporary_working_directory(self._dir)
            )
            self._runner = await stack.enter_async_context(
                AsyncProcessRunner(sidebar_width=5)
            )

            if self._serial_port:
                from serial import Serial

                udp_port = self._get_primary_udp_output_address()
                assert udp_port is not None

                try:
                    serial_port = stack.enter_context(
                        closing(Serial(self._serial_port)), baudrate=921600
                    )
                except Exception:
                    # maybe it's a virtual serial port where we cannot set the
                    # baud rate?
                    serial_port = stack.enter_context(
                        closing(Serial(self._serial_port))  # , baudrate=921600))
                    )

                await stack.enter_async_context(
                    UDPSerialBridge(udp_port, serial_port).use()
                )

            try:
                yield SimulatedDroneSwarmContext(self)
            finally:
                self._nursery = None
                self._runner = None
                self._swarm_dir = None

    def _get_primary_udp_output_address(self) -> Optional[str]:
        return (
            self._gcs_address
            if self._gcs_address
            else "127.0.0.1:14550"
            if self._serial_port
            else None
        )

    def _request_stop(self):
        """Requests the simulator processes of the swarm to stop."""
        if self._runner:
            self._runner.request_stop()
        if self._nursery:
            self._nursery.cancel_scope.cancel()

    async def _start_simulated_drone(
        self,
        home: Point,
        heading: Optional[float] = None,
    ) -> ManagedAsyncProcess:
        """Starts the simulator process for a single drone with the given
        parameters.

        Parameters:
            home: the home position of the drone, in flat Earth coordinates
            heading: the initial heading of the drone; `None` means to use the
                swarm-specific default
        """
        assert self._runner is not None
        assert self._swarm_dir is not None

        geodetic_home = self._coordinate_system.to_gps((home[0], home[1], 0))
        heading = float(heading) if heading is not None else self._default_heading

        index = next(self._index_generator)

        drone_id = "{0:03}".format(index)
        drone_dir = self._swarm_dir / "drones" / drone_id
        drone_fs_dir = drone_dir / "fs"

        own_param_file = drone_dir / "default.param"

        tcp_port = self._tcp_base_port + index - 1 if self._tcp_base_port else None

        # Determine where the UDP status packets should be sent. If we have a
        # GCS address, send them there. If we don't, but we need to aggregate
        # the output to a serial port, send them to a dummy UDP port and then
        # aggregate the packets in a separate task (which we assume already
        # exists in the background)
        primary_udp_output = self._get_primary_udp_output_address()

        await AsyncPath(drone_dir).mkdir(parents=True, exist_ok=True)  # type: ignore

        async with await open_file(own_param_file, "wb+") as fp:
            for param_source in self._params:
                if isinstance(param_source, str):
                    if param_source.startswith("embedded://"):
                        param_source = use_embedded_param_file(
                            param_source[len("embedded://") :].lstrip("/")
                        )
                    else:
                        param_source = Path(param_source)

                if isinstance(param_source, tuple):
                    name, value = param_source
                    await fp.write(f"{name}\t{value}\n".encode("utf-8"))
                elif hasattr(param_source, "__enter__"):
                    with param_source as path:
                        await copy_file_async(path, fp)
                        await fp.write(b"\n")
                elif param_source is not None:
                    await copy_file_async(param_source, fp)
                    await fp.write(b"\n")

            await fp.write(f"SYSID_THISMAV\t{index}\n".encode("utf-8"))

            if primary_udp_output:
                # We need the first serial port for primary telemetry
                await fp.write("SERIAL1_PROTOCOL\t2\n".encode("utf-8"))

            if self._multicast_address:
                # We need a second serial port for receiving multicast traffic
                # (which is used to simulate broadcast). At the same time, we
                # disable MAVLink forwarding to/from this port
                await fp.write("SERIAL2_PROTOCOL\t2\n".encode("utf-8"))
                await fp.write("SERIAL2_OPTIONS\t1024\n".encode("utf-8"))

            if tcp_port:
                # We also need a serial port for receiving direct traffic from
                # the TCP port associated to the UAV.
                await fp.write("SERIAL0_PROTOCOL\t2\n".encode("utf-8"))

        await AsyncPath(drone_fs_dir).mkdir(parents=True, exist_ok=True)  # type: ignore

        process = await start_simulator(
            self._executable,
            runner=self._runner,
            name=str(index),
            param_file=own_param_file,
            home=geodetic_home,
            heading=heading,
            index=index - 1,
            model=self._model,
            cwd=drone_fs_dir,
            uarts={
                # Serial port 0 is for direct access via TCP. This corresponds to
                # SERIAL0 in the params, which is also a "trusted" port on
                # ArduPilot so it can be used to set up MAVLink signing.
                0: (f"tcp:{tcp_port}" if tcp_port else "none"),
                # Serial port 1 is the "primary output" where the UDP status packets
                # are sent. This corresponds to SERIAL1 in the params.
                1: (
                    f"udpclient:{primary_udp_output}" if primary_udp_output else "none"
                ),
                # Serial port 2 is the port for receiving multicast traffic (which is
                # used to simulate broadcasts). This corresponds to SERIAL2 in
                # the params.
                2: (
                    f"mcast:{self._multicast_address}"
                    if self._multicast_address
                    else "none"
                ),
            },
        )

        return process


def use_embedded_param_file(name: str) -> ContextManager[Path]:
    """Context manager that ensures that the embedded ArduPilot SITL parameter
    file with the given name is accessible on the filesystem, extracting it to
    a temporary file if needed. The temporary file (if any) is cleaned up when
    exiting the context.
    """
    return resource_path(f"{__package__}.resources", name)


class SimulatedDroneSwarmContext:
    """Context object returned from `SimulatedDroneSwarm.use()` that the user
    can use to add new drones to the swarm.
    """

    def __init__(self, swarm: SimulatedDroneSwarm):
        self._swarm = swarm

    async def add_drone(
        self,
        home: Point,
        heading: Optional[float] = None,
    ) -> ManagedAsyncProcess:
        """Adds a new drone to the swarm and starts the corresponding simulator
        process.

        Parameters:
            home: the home position of the drone, in flat Earth coordinates
            heading: the initial heading of the drone; `None` means to use the
                swarm-specific default
        """
        return await self._swarm._start_simulated_drone(home=home, heading=heading)

    def request_stop(self):
        """Requests the drone swarm to stop the simulation and shut down
        gracefully.
        """
        self._swarm._request_stop()
