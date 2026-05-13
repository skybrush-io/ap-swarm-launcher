__all__ = ("DEFAULT_GCS_PORT", "DEFAULT_LISTENER_PORT")

DEFAULT_GCS_PORT = 14550
"""Default UDP port for the ground control station (GCS) on localhost. This is where
all simulated drones will send their telemetry data to.
"""

DEFAULT_LISTENER_PORT = 14555
"""Default UDP port where the simulated drones listen for broadcast traffic from the
GCS.
"""
