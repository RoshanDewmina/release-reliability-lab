from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from reliability_lab.adapters import ProbeFailure, model_adapter, wait_until_responding
from reliability_lab.processes import ProcessRegistry


def start_fixture(processes: ProcessRegistry, profile: str, port: int, tmp_path: Path) -> None:
    processes.start(
        profile,
        [
            sys.executable,
            "-m",
            "reliability_lab.fixture_service",
            "--profile",
            profile,
            "--port",
            str(port),
        ],
        Path.cwd(),
        os.environ.copy(),
        tmp_path / f"{profile}.log",
    )
    wait_until_responding(f"http://127.0.0.1:{port}", 5)


def test_model_gate_accepts_full_contract(free_ports, tmp_path: Path) -> None:
    port, _ = free_ports
    processes = ProcessRegistry()
    try:
        start_fixture(processes, "model", port, tmp_path)
        result = model_adapter(port).gate(1)
        assert result["health"]["version"] == "wine-logreg-v1"
        assert result["probe"]["probability_sum"] == pytest.approx(1.0)
    finally:
        processes.stop_all()


@pytest.mark.parametrize("profile", ["wrong-model", "broken"])
def test_model_gate_fails_closed_for_wrong_or_unhealthy_release(
    profile: str, free_ports, tmp_path: Path
) -> None:
    port, _ = free_ports
    processes = ProcessRegistry()
    try:
        start_fixture(processes, profile, port, tmp_path)
        with pytest.raises(ProbeFailure):
            model_adapter(port).gate(1)
    finally:
        processes.stop_all()
