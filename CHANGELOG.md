# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-10-20

### Added

- Added `--param-preset` to select which embedded parameter files are applied
  before the `--param` sources. `copter` (the default) applies
  `copter-default.parm` and `copter-skybrush.parm`. `none` applies no embedded
  files, so callers can supply a full parameter set via `--param` (e.g. for
  non-Copter SITL binaries).

## [1.1.0] - 2026-07-15

### Added

- The simulator now sets both the `SYSID_THISMAV` and the `MAV_SYSID` parameters to
  specify the system ID of a simulated drone. Earlier ArduPilot versions use
  `SYSID_THISMAV` while later versions use `MAV_SYSID`.

- Added `--rc-base-port` argument to open UDP ports for submitting simulated RC inputs
  to the drones. The base port is used for the first drone, and subsequent drones use
  incremented ports.

## [1.0.0] - 2025-10-02

This is the release that serves as a basis for changelog entries above. Refer
to the commit logs for changes affecting this version and earlier versions.
