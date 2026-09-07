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


@pytest.mark.parametrize("reap_first", [True, False])
def test_cleanup_stops_descendant_after_owned_group_leader_exits(
    tmp_path: Path, reap_first: bool
) -> None:
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
        if reap_first:
            leader.process.wait(timeout=3)
        else:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                state = subprocess.run(
                    ["ps", "-p", str(leader.pid), "-o", "stat="],
                    capture_output=True, text=True, check=False,
                ).stdout.strip()
                if state.startswith("Z"):
                    break
                time.sleep(.02)
            assert state.startswith("Z")
            assert leader.process.returncode is None  # No poll/wait before cleanup.

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


def test_failed_ownership_check_retains_registration_for_retry(tmp_path, monkeypatch):
    processes = ProcessRegistry()
    owned = processes.start("retry", [sys.executable, "-c", "import time;time.sleep(30)"],
                            Path.cwd(), os.environ.copy(), tmp_path / "retry.log")
    try:
        with monkeypatch.context() as patch:
            def refuse(_managed):
                raise UnownedProcess("injected ownership inspection failure")
            patch.setattr(processes, "_owned_group_exists", refuse)
            with pytest.raises(RuntimeError, match="failed to stop"):
                processes.stop_all()
            assert processes.owned_names() == ("retry",)
            assert owned.process.poll() is None
        processes.stop_all()
        assert not processes.owned_names() and owned.process.poll() is not None
    finally:
        processes.stop_all()
