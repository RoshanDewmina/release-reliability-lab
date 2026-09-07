from __future__ import annotations

import os
import subprocess
import sys
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
