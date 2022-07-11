"""GPS coordinates of commonly used locations, and location-related utility
functions.
"""

from dataclasses import dataclass
from math import cos, degrees, radians, sin, sqrt
from typing import Dict, Optional, Tuple

__all__ = ("GPSCoordinate", "LOCATIONS", "parse_location")


@dataclass(frozen=True)
class GPSCoordinate:
    """Simple immutable data class representing a latitude-longitude-AMSL
    triplet.
    """

    lon: float
    lat: float
    amsl: float


NULL_ISLAND = GPSCoordinate(0, 0, 0)
"""Null Island as a GPS coordinate."""


class WGS84:
    """WGS84 ellipsoid model parameters for Earth."""

    ####################################################################
    # Defining parameters of WGS84 come first

    EQUATORIAL_RADIUS_IN_METERS: float = 6378137.0
    """Equatorial radius of Earth in the WGS ellipsoid model"""

    INVERSE_FLATTENING: float = 298.257223563
    """Inverse flattening of Earth in the WGS ellipsoid model"""

    GRAVITATIONAL_CONSTANT_TIMES_MASS: float = 3.986005e14
    """Gravitational constant times Earth's mass"""

    ROTATION_RATE_IN_RADIANS_PER_SEC: float = 7.2921151467e-5
    """Earth's rotation rate [rad/sec]"""

    ####################################################################
    # Non-defining parameters of WGS84 are below

    FLATTENING = 1.0 / INVERSE_FLATTENING
    """Flattening of Earth in the WGS ellipsoid model"""

    ECCENTRICITY = (FLATTENING * (2 - FLATTENING)) ** 0.5
    """Eccentricity of Earth in the WGS ellipsoid model"""

    ECCENTRICITY_SQUARED = ECCENTRICITY**2
    """Square of the eccentricity of Earth in the WGS ellipsoid model"""

    POLAR_RADIUS_IN_METERS = EQUATORIAL_RADIUS_IN_METERS * (1 - FLATTENING)
    """Polar radius of Earth in the WGS ellipsoid model"""

    MEAN_RADIUS_IN_METERS = (
        2 * EQUATORIAL_RADIUS_IN_METERS + POLAR_RADIUS_IN_METERS
    ) / 3
    """Mean radius of Earth in the WGS ellipsoid model, as defined by IUGG"""


class FlatEarthToGPSCoordinateTransformation:
    """Transformation that converts flat Earth coordinates to GPS
    coordinates and vice versa.
    """

    _origin: GPSCoordinate = NULL_ISLAND
    _xmul: float
    _ymul: float
    _zmul: float
    _sin_alpha: float
    _cos_alpha: float

    @staticmethod
    def _normalize_type(type: str) -> str:
        """Returns the normalized name of the given coordinate system type.

        Raises:
            ValueError: if type is not a known coordinate system name
        """
        normalized = None
        if len(type) == 3:
            type = type.lower()
            if type in ("neu", "nwu", "ned", "nwd"):
                normalized = type

        if not normalized:
            raise ValueError("unknown coordinate system type: {0!r}".format(type))

        return normalized

    def __init__(
        self,
        origin: GPSCoordinate = NULL_ISLAND,
        orientation: float = 0.0,
        type: str = "nwu",
    ):
        """Constructor.

        Parameters:
            origin: origin of the flat Earth coordinate system, in GPS
                coordinates. Altitude component is ignored. The coordinate will
                be copied.
            orientation: orientation of the X axis of the coordinate system, in
                degrees, relative to North (zero degrees), increasing in CW
                direction.
            type: orientation of the coordinate system; can be `"neu"`
                (North-East-Up), `"nwu"` (North-West-Up), `"ned"`
                (North-East-Down) or `"nwd"` (North-West-Down)
        """
        self._origin = origin
        self._orientation = float(orientation)
        self._type = self._normalize_type(type)
        self._recalculate()

    @property
    def origin(self) -> GPSCoordinate:
        """The origin of the coordinate system."""
        return self._origin

    @origin.setter
    def origin(self, value: GPSCoordinate) -> None:
        if self._origin != value:
            self._origin = value
            self._recalculate()

    @property
    def orientation(self) -> float:
        """The orientation of the X axis of the coordinate system, in degrees,
        relative to North (zero degrees), increasing in clockwise direction.
        """
        return self._orientation

    @orientation.setter
    def orientation(self, value: float) -> None:
        if self._orientation != value:
            self._orientation = value
            self._recalculate()

    @property
    def type(self) -> str:
        """The type of the coordinate system."""
        return self._type

    @type.setter
    def type(self, value: str) -> None:
        if self._type != value:
            self._type = value
            self._recalculate()

    def _recalculate(self) -> None:
        """Recalculates some cached values that are re-used across different
        transformations.
        """
        earth_radius = WGS84.EQUATORIAL_RADIUS_IN_METERS
        eccentricity_sq = WGS84.ECCENTRICITY_SQUARED

        origin_lat_in_radians = radians(self._origin.lat)

        x = 1 - eccentricity_sq * (sin(origin_lat_in_radians) ** 2)
        self._r1 = earth_radius * (1 - eccentricity_sq) / (x**1.5)
        self._r2_over_cos_origin_lat_in_radians = (
            earth_radius / sqrt(x) * cos(origin_lat_in_radians)
        )

        self._sin_alpha = sin(radians(self._orientation))
        self._cos_alpha = cos(radians(self._orientation))

        self._xmul = 1
        self._ymul = 1 if self._type[1] == "e" else -1
        self._zmul = 1 if self._type[2] == "u" else -1

    def to_flat_earth(self, coord: GPSCoordinate) -> Tuple[float, float, float]:
        """Converts the given GPS coordinates to flat Earth coordinates.

        Parameters:
            coord: the coordinate to convert

        Returns:
            the converted coordinate
        """
        x, y = (
            radians(coord.lat - self._origin.lat) * self._r1,
            radians(coord.lon - self._origin.lon)
            * self._r2_over_cos_origin_lat_in_radians,
        )
        x, y = (
            x * self._cos_alpha + y * self._sin_alpha,
            -x * self._sin_alpha + y * self._cos_alpha,
        )
        z = coord.amsl - self._origin.amsl
        return (x * self._xmul, y * self._ymul, z * self._zmul)

    def to_gps(self, coord: Tuple[float, float, float]) -> GPSCoordinate:
        """Converts the given flat Earth coordinates to GPS coordinates.

        Parameters:
            coord: the coordinate to convert

        Returns:
            the converted coordinate
        """
        x, y, z = (coord[0] / self._xmul, coord[1] / self._ymul, coord[2] / self._zmul)

        x, y = (
            x * self._cos_alpha - y * self._sin_alpha,
            x * self._sin_alpha + y * self._cos_alpha,
        )

        lat = degrees(x / self._r1)
        lon = degrees(y / self._r2_over_cos_origin_lat_in_radians)

        return GPSCoordinate(
            lat=lat + self._origin.lat,
            lon=lon + self._origin.lon,
            amsl=z + self._origin.amsl,
        )


@dataclass
class LocationDefinition:
    origin: GPSCoordinate
    orientation: float = 0

    _coordinate_system: Optional[FlatEarthToGPSCoordinateTransformation] = None

    @property
    def amsl(self) -> Optional[float]:
        return self.origin.amsl

    @property
    def coordinate_system(self):
        if self._coordinate_system is None:
            self._coordinate_system = FlatEarthToGPSCoordinateTransformation(
                origin=self.origin, orientation=self.orientation, type="nwu"
            )
        return self._coordinate_system


def parse_location(value: str) -> LocationDefinition:
    """Constructs a LocationDefinition object from a `lat,lon,amsl,orientation`
    representation, or from the name of well-known location from the
    `LOCATIONS` dictionary.

    The altitude above mean sea level and the orientation are optional and they
    default to zero.
    """
    if value in LOCATIONS:
        return LOCATIONS[value]

    parts = [part.strip() for part in value.split(",")]
    if len(parts) < 2:
        raise ValueError("GPS coordinate needs at least a latitude and a longitude")
    if len(parts) > 4:
        raise ValueError(
            "GPS coordinate needs at most a latitude, a longitude, an "
            "altitude and an orientation"
        )

    return LocationDefinition(
        origin=GPSCoordinate(
            lat=float(parts[0]),
            lon=float(parts[1]),
            amsl=float(parts[2]) if len(parts) > 2 else 0,
        ),
        orientation=float(parts[3]) if len(parts) > 3 else 0,
    )


LOCATIONS: Dict[str, LocationDefinition] = {
    "CMAC": LocationDefinition(
        GPSCoordinate(lat=-35.363261, lon=149.165230, amsl=584), orientation=353
    )
}
"""Dictionary of commonly used locations, mapping the name of a location to a
location definition.
"""

DEFAULT_LOCATION = LOCATIONS["CMAC"]
"""Default location to use in the codebase."""
