from __future__ import annotations

import os
import signal
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import IO


class PortUnavailable(RuntimeError):
    pass


class UnownedProcess(RuntimeError):
    pass


def assert_port_free(host: str, port: int) -> None:
    with socket.socket() as probe:
        probe.settimeout(0.15)
        if probe.connect_ex((host, port)) == 0:
            raise PortUnavailable(f"refusing to use occupied port {host}:{port}")


@dataclass(slots=True)
class ManagedProcess:
    name: str
    process: subprocess.Popen[bytes]
    command: list[str]
    cwd: Path
    log_path: Path
    log_file: IO[bytes]

    @property
    def pid(self) -> int:
        return self.process.pid


class ProcessRegistry:
    def __init__(self) -> None:
        self._owned: dict[str, ManagedProcess] = {}

    def start(
        self,
        name: str,
        command: list[str],
        cwd: Path,
        environment: dict[str, str],
        log_path: Path,
    ) -> ManagedProcess:
        if name in self._owned:
            raise ValueError(f"process name already owned: {name}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = log_path.open("wb")
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            log_file.close()
            raise
        managed = ManagedProcess(name, process, command, cwd, log_path, log_file)
        self._owned[name] = managed
        return managed

    def interrupt(self, name: str, *, force: bool = False, timeout: float = 3.0) -> int:
        managed = self._owned.get(name)
        if managed is None:
            raise UnownedProcess(f"refusing to signal unowned process: {name}")
        try:
            if managed.process.poll() is None:
                try:
                    os.killpg(managed.pid, signal.SIGKILL if force else signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    managed.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    os.killpg(managed.pid, signal.SIGKILL)
                    managed.process.wait(timeout=timeout)
            return managed.process.returncode
        finally:
            managed.log_file.close()
            self._owned.pop(name, None)

    def stop_all(self) -> None:
        failures = []
        for name in list(reversed(self._owned)):
            try:
                self.interrupt(name)
            except Exception as exc:
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError(f"failed to stop owned processes: {failures}")

    def owned_names(self) -> tuple[str, ...]:
        return tuple(self._owned)
