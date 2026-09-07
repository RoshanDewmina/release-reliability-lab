from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from reliability_lab.app import create_app
from reliability_lab.reporting import write_report


def test_report_write_is_atomic_and_ui_api_reads_latest(tmp_path: Path) -> None:
    path = tmp_path / "run.json"
    write_report(path, {"status": "passed", "run_id": "first"})
    write_report(path, {"status": "failed", "run_id": "second"})
    assert json.loads(path.read_text()) == {"status": "failed", "run_id": "second"}
    assert not list(tmp_path.glob("*.tmp"))

    client = TestClient(create_app(path))
    assert client.get("/health").json() == {
        "status": "ok",
        "service": "release-reliability-lab",
        "version": "0.1.0",
    }
    assert client.get("/api/report").json()["run_id"] == "second"
    assert client.get("/").status_code == 200


def test_missing_report_returns_404(tmp_path: Path) -> None:
    client = TestClient(create_app(tmp_path / "missing.json"))
    assert client.get("/api/report").status_code == 404
