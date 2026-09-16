#!/usr/bin/env python3
"""Stateful launchctl test double for LocalFlow launcher integration tests."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def load_state(path: Path) -> dict:
    return json.loads(path.read_text())


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, sort_keys=True))


def log_call(path: Path, arguments: list[str]) -> None:
    with path.open("a") as stream:
        stream.write(json.dumps(arguments) + "\n")


def main() -> int:
    state_path = Path(os.environ["FAKE_LAUNCHCTL_STATE"])
    log_path = Path(os.environ["FAKE_LAUNCHCTL_LOG"])
    arguments = sys.argv[1:]
    log_call(log_path, arguments)
    state = load_state(state_path)

    if not arguments:
        print("missing command", file=sys.stderr)
        return 64

    command = arguments[0]
    if command == "print":
        if "unloading_remaining" in state:
            remaining = int(state["unloading_remaining"])
            if remaining > 0:
                state["unloading_remaining"] = remaining - 1
                save_state(state_path, state)
            else:
                state["loaded"] = False
                state["running"] = False
                state.pop("pid", None)
                state.pop("unloading_remaining", None)
                save_state(state_path, state)
        inspection_status = int(state.get("inspection_status", 0))
        if inspection_status:
            print(state.get("inspection_error", "unexpected inspection error"),
                  file=sys.stderr)
            return inspection_status
        if not state.get("loaded", False):
            label = arguments[1].split("/", 2)[-1]
            print("Bad request.", file=sys.stderr)
            print(
                f'Could not find service "{label}" in domain for user gui: '
                f'{state.get("uid", 501)}',
                file=sys.stderr,
            )
            return 113
        running = state.get("running", False)
        print(f'{arguments[1]} = {{')
        if not state.get("omit_state", False):
            print(f'\tstate = {"running" if running else "not running"}')
        if running and state.get("include_pid", True):
            print(f'\tpid = {state.get("pid", 41001)}')
        print("}")
        return 0

    if command == "kickstart":
        if not state.get("loaded", False):
            print("service is not loaded", file=sys.stderr)
            return 113
        if state.get("kickstart_status", 0):
            print(state.get("kickstart_error", "kickstart failed"),
                  file=sys.stderr)
            return int(state["kickstart_status"])
        if state.get("kickstart_runs", True):
            state["running"] = True
            state["pid"] = state.get("next_pid", 42001)
        save_state(state_path, state)
        return 0

    if command == "bootstrap":
        eio_failures = int(state.get("bootstrap_eio_failures", 0))
        if eio_failures > 0:
            state["bootstrap_eio_failures"] = eio_failures - 1
            save_state(state_path, state)
            print("Bootstrap failed: 5: Input/output error", file=sys.stderr)
            return 5
        if state.get("loaded", False):
            print("Bootstrap failed: 5: Input/output error", file=sys.stderr)
            return 5
        if state.get("bootstrap_status", 0):
            print(state.get("bootstrap_error", "bootstrap failed"),
                  file=sys.stderr)
            return int(state["bootstrap_status"])
        state["loaded"] = True
        state["running"] = state.get("bootstrap_runs", True)
        if state["running"]:
            state["pid"] = state.get("next_pid", 43001)
        save_state(state_path, state)
        return 0

    if command == "bootout":
        if not state.get("loaded", False):
            print("service is not loaded", file=sys.stderr)
            return 113
        if state.get("bootout_status", 0):
            print(state.get("bootout_error", "bootout failed"),
                  file=sys.stderr)
            return int(state["bootout_status"])
        state["running"] = False
        state.pop("pid", None)
        linger = int(state.get("bootout_linger_prints", 0))
        if linger > 0:
            state["unloading_remaining"] = linger
        else:
            state["loaded"] = False
        save_state(state_path, state)
        return 0

    if command == "kill":
        if not state.get("loaded", False):
            print("service is not loaded", file=sys.stderr)
            return 113
        state["running"] = False
        state.pop("pid", None)
        save_state(state_path, state)
        return 0

    print(f"unsupported command: {command}", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
