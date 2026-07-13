# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Pyaarlo is a Python library (>=3.7, setup.py-based) that provides asynchronous access to Arlo security cameras. It maintains a persistent event stream to Arlo's servers on a background thread and updates internal device state as events arrive.

## Commands

```bash
# Install for development (needed before tests will run — they import the package)
pip install -e .

# Run all tests (use python3; plain `python` may not exist)
python3 -m unittest discover -s tests

# Run one test file / one test
python3 -m unittest tests.test_cfg
python3 -m unittest tests.test_cfg.TestArloCfg.test_scheme
```

The `pyaarlo` console script (`pyaarlo.main:main_func`, click-based) provides CLI access: `pyaarlo -u USER -p PASS <command>` where the commands are `list`, `dump`, `anonymize`, `encrypt`, `decrypt`, and `camera` (which takes a `start-stream|stop-stream|last-thumbnail` action). There is no lint/format configuration in this repo.

## Architecture

Everything hangs off a single `PyArlo` instance (`pyaarlo/__init__.py`), which logs in, discovers devices, and owns the shared services. Every device object holds a reference back to it for config, logging, state storage, and request dispatch.

**Data flow:** writes and reads are decoupled. Setting an attribute (e.g. `base.mode = 'armed'`) sends an HTTP request to Arlo; internal state only updates when Arlo echoes the change back over the event stream, at which point registered callbacks fire. Reading immediately after writing can return stale data unless `synchronous_mode=True` is passed to `PyArlo`. Keep this in mind for any code that sets then reads device state.

Key components:

- **`ArloBackEnd` (`backend.py`)** — all communication with Arlo: authentication (Cloudflare bypass via cloudscraper/curl_cffi, 2FA delegated to `tfa.py`), HTTP requests, and the event stream. The stream backend defaults to `auto`: MQTT if the account exposes MQTT topics, otherwise SSE (`sseclient.py`); see `_select_backend()`. `_event_dispatcher()` parses Arlo's inconsistent packet formats (catalogued by type in `docs/packets.md`) and routes them to devices registered via `add_listener()`.
- **`ArloBackground` (`background.py`)** — a priority job-queue worker thread. Event callbacks, periodic refreshes, and async writes (`notify()`) are scheduled onto it (`run()`, `run_every()`, etc.). Note that plain `get()`/`post()` requests default to `wait_for="response"` and block the calling thread inline — relevant when debugging blocking behavior.
- **Device model** — `ArloSuper` (`super.py`) is the root class: attribute store, event handling, and `add_attr_callback()` registration. `ArloDevice` (`device.py`) extends it for physical devices, with `ArloChildDevice` (also `device.py`) beneath it for devices that hang off a base station. Concrete classes are `ArloBase`, `ArloCamera`, `ArloDoorBell`, `ArloLight`, `ArloSensor`, plus `ArloLocation` (`location.py`) for the newer location-based API.
- **`ArloStorage` (`storage.py`)** — the in-memory state database keyed by `class_name/device_id/attribute` (see `ArloSuper._to_storage_key()`), pickled to disk across restarts when `save_state` is on. Device `attribute()` reads go through here.
- **`ArloMediaLibrary` (`media.py`)** — keeps the recording/snapshot library up to date as media events arrive.
- **`ArloRatls` (`ratls.py`) + `security_utils.py`** — direct TLS connection to a base station for local-storage (SmartHub) access.
- **`ArloCfg` (`cfg.py`)** — every `PyArlo(**kwargs)` option is parsed into properties here. **`constant.py`** holds API paths, model-ID prefixes, and defaults.

### Adding support for new device models

Device discovery in `PyArlo.__init__` classifies devices by `deviceType` and `modelId` prefix. New models get a `MODEL_*` constant in `constant.py` and are added to the relevant `startswith()` tuples in `__init__.py` — WiFi-direct devices that act as their own base station go in the base-station tuple (see the Pro 6 commit `6de0c11` as a template). Camera classes also map model IDs to capabilities in `ArloCamera.has_capability()`.

## Tests

Tests never hit the network: `tests/arlo.py` provides a stub `PyArlo` that wires up only `ArloCfg`, and coverage is currently limited to config parsing (`test_cfg.py`). Real-interaction debugging is done with the CLI's `dump`/`anonymize` commands and the packet formats described in `docs/packets.md`.
