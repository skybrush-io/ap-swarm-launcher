"""Command-line interface entrypoint for the ArduPilot SITL swarm launcher tool."""

import sys

from argparse import ArgumentParser
from functools import partial
from pathlib import Path
from random import normalvariate
from typing import List, Optional, Sequence, Tuple, Union

from ap_swarm_launcher.formations import create_grid_formation
from ap_swarm_launcher.locations import (
    DEFAULT_LOCATION,
    LocationDefinition,
    parse_location,
)
from ap_swarm_launcher.sitl import SimulatedDroneSwarm
from ap_swarm_launcher.utils import route_local_broadcast_traffic_to_multicast


def parse_parameter(value: str) -> Union[Path, Tuple[str, float]]:
    name, sep, value = value.partition("=")
    if sep:
        return name.strip(), float(value)
    else:
        path = Path(name.strip())
        if not path.is_file():
            raise RuntimeError(f"Param file does not exist: {name!r}")
        return path


def create_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="ap-sitl-swarm")

    parser.add_argument(
        "--tcp-base-port",
        default=None,
        type=int,
        metavar="PORT",
        help="open TCP connections for each drone, starting from PORT",
    )
    parser.add_argument(
        "--no-udp",
        dest="use_udp",
        default=True,
        action="store_false",
        help="do not send status packets to UDP port 14550",
    )
    parser.add_argument(
        "-d",
        "--data-dir",
        default=None,
        type=Path,
        metavar="DIR",
        help="store the filesystem and parameters of the simulated drones in "
        "the given DIR. Creates a temporary directory when omitted.",
    )
    parser.add_argument(
        "--home",
        default=DEFAULT_LOCATION,
        type=parse_location,
        help=(
            "the origin and orientation of the initial formation of the swarm "
            "(latitude,longitude,amsl,orientation) or the name of a well-known "
            "location)"
        ),
    )
    parser.add_argument(
        "-n",
        "--num-drones",
        default=1,
        type=int,
        help="use COUNT drones in the swarm",
        metavar="COUNT",
    )
    parser.add_argument(
        "--num-drones-per-row",
        default=None,
        type=int,
        help="use COUNT drones per row in the takeoff grid",
        metavar="COUNT",
    )
    parser.add_argument(
        "--param",
        default=[],
        type=parse_parameter,
        action="append",
        metavar="PARAM",
        help="names of parameter files or parameters in the format NAME=VALUE that "
        "should be passed to the simulated drones, on top of the default parameter "
        "settings",
    )
    parser.add_argument(
        "--spacing",
        type=float,
        default=3.0,
        metavar="DIST",
        help="use DIST meters of spacing between drones in the initial grid formation",
    )
    parser.add_argument(
        "--pos-noise",
        type=float,
        default=0.0,
        metavar="DIST",
        help=(
            "perturb the initial drone positions with Gaussian noise with a "
            "standard deviation of DIST"
        ),
    )
    parser.add_argument(
        "--yaw-noise",
        type=float,
        default=0.0,
        metavar="ANGLE",
        help=(
            "perturb the yaw angles with Gaussian noise with a standard "
            "deviation of ANGLE"
        ),
    )

    parser.add_argument(
        "sitl_executable",
        metavar="SITL_EXECUTABLE",
        help="path to the ArduPilot SITL executable to use",
    )
    return parser


async def run(
    sitl_executable: Path,
    data_dir: Optional[Path] = None,
    home: LocationDefinition = DEFAULT_LOCATION,
    num_drones: int = 1,
    param: Sequence[Union[Path, Tuple[str, float]]] = (),
    spacing: float = 3,
    num_drones_per_row: Optional[int] = None,
    pos_noise: float = 0.0,
    yaw_noise: float = 0.0,
    tcp_base_port: Optional[int] = None,
    use_udp: bool = True,
) -> None:
    gcs_address = "127.0.0.1:14550" if use_udp else None
    multicast_address = "239.255.67.77:14555"

    sitl_executable = Path.cwd() / sitl_executable

    if num_drones_per_row is None:
        num_drones_per_row = max(1, int(round(num_drones**0.5)))

    grid = create_grid_formation(num_drones_per_row, spacing, pos_noise)

    param_sources: List[Union[Path, str, Tuple[str, float]]] = [
        "embedded://copter-skybrush.parm"
    ]
    param_sources.extend(param)

    async with SimulatedDroneSwarm(
        sitl_executable,
        dir=data_dir,
        params=param_sources,
        coordinate_system=home.coordinate_system,
        amsl=home.origin.amsl,
        gcs_address=gcs_address,
        multicast_address=multicast_address,
        tcp_base_port=tcp_base_port,
    ).use() as swarm:
        for i in range(num_drones):
            await swarm.add_drone(
                home=grid(i),
                heading=(home.orientation + normalvariate(0, yaw_noise)) % 360,
            )

        # We need to re-route broadcast traffic sent to the port of the
        # multicast address because Skybrush will send broadcast traffic
        # to 127.0.0.1
        await route_local_broadcast_traffic_to_multicast(multicast_address)


def start():
    from trio import run as run_async

    parser = create_parser()
    options = parser.parse_args()

    try:
        exit_code = run_async(partial(run, **vars(options)))
    except KeyboardInterrupt:
        exit_code = 0

    sys.exit(exit_code)
