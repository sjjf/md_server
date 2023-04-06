# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project (tries to) adhere to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]
### Changed

### Added

### Fixed

## [0.6.2] - 2023-04-06
### Added
- Support for extracting metadata from the domain XML to pass configuration
  items to the mdserver. Currently only used for a userdata prefix that
  overrides the default search patterns, may be used for more later.
- Support for presenting existing config items to templates, to avoid
  duplicating values unnecessarily.
- Simple indicator of service location (defaults to the hostname).

### Fixed
- mdserver.service unit needs to wait on network-online to make sure it can
  bind to the specified address.

## [0.6.1] - 2023-01-26
### Changed
- Updated build configuration - added a `pyproject.toml` file, and updated
  a number of elements of the build system.
- Updated `tests/run-test.sh` script to be a little more repeatable.
- Moved over to black for formatting/linting, since it's more consistent and
  repeatable than the previous rather ill-defined model.

### Added
- Support for splitting up the configuration file. This is intended to make it
  easier to support automatically generated or customised configurations (the
  updated `run-test.sh` script makes use of this).
- This CHANGELOG file.

### Fixed
- Spelling fixes in the README file.
- Properly document the libvirt network configuration (thanks to Alexander E.
  Fischer)
- Fix systemd unit file permissions - systemd complains if they're executable.
- Minor fixes in system integration script.
