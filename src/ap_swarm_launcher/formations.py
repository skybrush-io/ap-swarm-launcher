"""Takeoff area formations for the SITL drone swarm simulator."""

from random import random
from typing import Callable, Tuple

__all__ = ("create_grid_formation",)


Point = Tuple[float, float]
"""Type specification for a single 2D point that a formation returns."""


def create_grid_formation(
    num_drones_per_row: int, spacing: float = 1.0, noise: float = 0.0
) -> Callable[[int], Point]:
    def grid(index: int):
        row_index, col_index = divmod(index, num_drones_per_row)
        return (
            float(row_index) * spacing + (2 * random() - 1) * noise,
            -float(col_index) * spacing + (2 * random() - 1) * noise,
        )

    return grid
