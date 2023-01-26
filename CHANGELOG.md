# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project (tries to) adhere to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
