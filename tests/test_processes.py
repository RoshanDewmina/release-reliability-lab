from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from reliability_lab.processes import ProcessRegistry, UnownedProcess


def test_registry_refuses_unowned_process_name() -> None:
    with pytest.raises(UnownedProcess):
        ProcessRegistry().interrupt("not-started-here")


def test_cleanup_stops_owned_process_and_leaves_unregistered_process_alive(tmp_path: Path) -> None:
    external = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    processes = ProcessRegistry()
    try:
        owned = processes.start(
            "owned",
            [sys.executable, "-c", "import time; time.sleep(30)"],
            Path.cwd(),
            os.environ.copy(),
            tmp_path / "owned.log",
        )
        processes.stop_all()
        assert owned.process.poll() is not None
        assert external.poll() is None
    finally:
        if external.poll() is None:
            external.terminate()
            external.wait(timeout=3)
        processes.stop_all()


def test_cleanup_stops_descendant_after_owned_group_leader_exits(tmp_path: Path) -> None:
    child_pid_file = tmp_path / "child.pid"
    parent_code = (
        "import subprocess,sys; from pathlib import Path; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "Path(sys.argv[1]).write_text(str(child.pid))"
    )
    external = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    processes = ProcessRegistry()
    status = ""
    try:
        leader = processes.start(
            "leader",
            [sys.executable, "-c", parent_code, str(child_pid_file)],
            Path.cwd(),
            os.environ.copy(),
            tmp_path / "leader.log",
        )
        deadline = time.monotonic() + 3
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert child_pid_file.exists()
        child_pid = int(child_pid_file.read_text())
        leader.process.wait(timeout=3)

        processes.stop_all()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = subprocess.run(
                ["ps", "-p", str(child_pid), "-o", "stat="],
                capture_output=True,
                check=False,
                text=True,
            ).stdout.strip()
            if not status or status.startswith("Z"):
                break
            time.sleep(0.03)
        assert not status or status.startswith("Z")
        assert external.poll() is None
    finally:
        if external.poll() is None:
            external.terminate()
            external.wait(timeout=3)
        processes.stop_all()
