from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from reliability_lab.config import DEFAULT_DURABLE_REPO, DEFAULT_MODEL_REPO, RunConfig
from reliability_lab.orchestrator import ReliabilityRun


def test_fixture_run_is_labeled_and_completes_rollback(free_ports) -> None:
    durable_port, model_port = free_ports
    report = ReliabilityRun(
        RunConfig(
            mode="fixtures",
            durable_port=durable_port,
            model_port=model_port,
            load_requests=2,
        )
    ).execute()
    assert report["status"] == "passed", report.get("fatal_error")
    assert report["mode"] == "fixtures"
    assert report["release"]["candidate_rejected"] is True
    assert report["release"]["restored_release"] == "wine-logreg-v1"
    assert len(report["incidents"]) == 2


@pytest.mark.integration
def test_actual_candidate_is_rejected_and_known_model_release_is_restored(free_ports) -> None:
    if (
        not (DEFAULT_DURABLE_REPO / ".venv" / "bin" / "python").exists()
        or not (DEFAULT_MODEL_REPO / ".venv" / "bin" / "python").exists()
    ):
        pytest.skip("actual portfolio service environments are unavailable")
    dirty_dependencies = [
        repository
        for repository in (DEFAULT_DURABLE_REPO, DEFAULT_MODEL_REPO)
        if subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    ]
    if dirty_dependencies:
        pytest.skip(f"actual dependency repository is changing: {dirty_dependencies}")
    durable_port, model_port = free_ports
    report = ReliabilityRun(
        RunConfig(
            mode="actual",
            durable_repo=Path(DEFAULT_DURABLE_REPO),
            model_repo=Path(DEFAULT_MODEL_REPO),
            durable_port=durable_port,
            model_port=model_port,
            load_requests=2,
        )
    ).execute()
    assert report["status"] == "passed", report.get("fatal_error")
    assert report["release"]["candidate_rejected"] is True
    assert report["release"]["restored_release"] == "wine-logreg-v1"
    assert report["release"]["restored_registry_sha256"]
    assert report["dependencies"]["durable-workflows"]["revision"]
    assert report["dependencies"]["model-lifecycle-service"]["revision"]
    durable_incident = next(
        incident
        for incident in report["incidents"]
        if incident["id"] == "durable-owned-process-interruption"
    )
    assert "completed after restart" in durable_incident["data_check"]
