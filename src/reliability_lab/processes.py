from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import uuid
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
    process_group: int
    ownership_token: str

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
        ownership_token = uuid.uuid4().hex
        child_environment = {
            **environment,
            "RELIABILITY_LAB_PROCESS_TOKEN": ownership_token,
        }
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=child_environment,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            log_file.close()
            raise
        process_group = os.getpgid(process.pid)
        managed = ManagedProcess(
            name,
            process,
            command,
            cwd,
            log_path,
            log_file,
            process_group,
            ownership_token,
        )
        self._owned[name] = managed
        return managed

    def interrupt(self, name: str, *, force: bool = False, timeout: float = 3.0) -> int:
        managed = self._owned.get(name)
        if managed is None:
            raise UnownedProcess(f"refusing to signal unowned process: {name}")
        try:
            if self._owned_group_exists(managed):
                os.killpg(
                    managed.process_group,
                    signal.SIGKILL if force else signal.SIGTERM,
                )
                if not force:
                    deadline = time.monotonic() + timeout
                    while time.monotonic() < deadline:
                        managed.process.poll()
                        if not self._group_members(managed.process_group):
                            break
                        time.sleep(0.03)
                    if self._owned_group_exists(managed):
                        os.killpg(managed.process_group, signal.SIGKILL)
            try:
                managed.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if self._owned_group_exists(managed):
                    os.killpg(managed.process_group, signal.SIGKILL)
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

    def diagnostics(self, max_bytes_per_process: int = 4096) -> dict[str, str]:
        result = {}
        for name, managed in self._owned.items():
            managed.log_file.flush()
            try:
                content = managed.log_path.read_bytes()[-max_bytes_per_process:]
            except OSError as exc:
                result[name] = f"log unavailable: {type(exc).__name__}: {exc}"
            else:
                result[name] = content.decode(errors="replace")
        return result

    @staticmethod
    def _group_members(process_group: int) -> list[int]:
        output = subprocess.run(
            ["ps", "-axo", "pid=,pgid="],
            capture_output=True,
            check=True,
            text=True,
        ).stdout
        members = []
        for line in output.splitlines():
            fields = line.split()
            if len(fields) == 2 and int(fields[1]) == process_group:
                members.append(int(fields[0]))
        return members

    @classmethod
    def _owned_group_exists(cls, managed: ManagedProcess) -> bool:
        members = cls._group_members(managed.process_group)
        if not members:
            return False
        for pid in members:
            command = subprocess.run(
                ["ps", "eww", "-p", str(pid), "-o", "command="],
                capture_output=True,
                check=False,
                text=True,
            ).stdout
            if managed.ownership_token not in command:
                raise UnownedProcess(
                    f"refusing to signal reused or mixed process group {managed.process_group}"
                )
        return True
