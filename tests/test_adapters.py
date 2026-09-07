from __future__ import annotations

import os
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from reliability_lab.adapters import (
    ProbeFailure,
    model_adapter,
    validate_model_prediction,
    wait_until_responding,
)
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


VALID_PREDICTION = {
    "prediction": 0,
    "class_name": "cultivar_1",
    "probabilities": {"cultivar_1": 0.8, "cultivar_2": 0.1, "cultivar_3": 0.1},
    "model_version": "wine-logreg-v1",
}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body.update(prediction=999),
        lambda body: body.update(prediction=True),
        lambda body: body.update(class_name="cultivar_2"),
        lambda body: body.update(probabilities={"cultivar_1": -1.0, "cultivar_2": 2.0}),
        lambda body: body.update(
            probabilities={
                "cultivar_1": float("nan"),
                "cultivar_2": 0.5,
                "cultivar_3": 0.5,
            }
        ),
        lambda body: body.update(
            probabilities={"cultivar_1": 0.6, "cultivar_2": 0.3, "cultivar_3": 0.3}
        ),
        lambda body: body.pop("class_name"),
    ],
)
def test_prediction_contract_rejects_impossible_payloads(mutation) -> None:
    body = deepcopy(VALID_PREDICTION)
    mutation(body)
    with pytest.raises(ProbeFailure):
        validate_model_prediction(body, "wine-logreg-v1")


def test_prediction_contract_accepts_supported_strict_payload() -> None:
    result = validate_model_prediction(VALID_PREDICTION, "wine-logreg-v1")
    assert result["model_version"] == "wine-logreg-v1"
    assert result["prediction"] == 0
    assert result["class_name"] == "cultivar_1"
    assert result["probability_sum"] == pytest.approx(1.0)
