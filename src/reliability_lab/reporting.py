from __future__ import annotations

import json
import os
import platform
import subprocess
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def hardware_description() -> str:
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return platform.processor() or "unavailable"


def git_state(repository: Path) -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, check=True, text=True
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repository,
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    )
    return {"path": str(repository), "revision": revision, "dirty_tree": dirty}


class RunRecorder:
    def __init__(
        self,
        mode: str,
        dependencies: dict[str, Any],
        source: dict[str, Any],
        inputs: dict[str, Any],
    ):
        self.report: dict[str, Any] = {
            "schema_version": 1,
            "run_id": str(uuid.uuid4()),
            "mode": mode,
            "started_at": utc_now(),
            "timestamp": utc_now(),
            "completed_at": None,
            "status": "running",
            "exit_status": None,
            "source_revision": source["revision"],
            "dirty_tree": source["dirty_tree"],
            "command": None,
            "inputs": inputs,
            "environment": {
                "os": platform.platform(),
                "architecture": platform.machine(),
                "python": platform.python_version(),
                "hardware": hardware_description(),
                "logical_cpu_count": os.cpu_count(),
            },
            "dependencies": dependencies,
            "actions": [],
            "incidents": [],
            "release": {},
            "load": [],
            "limitations": [],
        }

    @contextmanager
    def action(self, name: str, **details: Any) -> Iterator[dict[str, Any]]:
        started_at = utc_now()
        started = time.perf_counter()
        result: dict[str, Any] = {}
        try:
            yield result
        except BaseException as exc:
            self.report["actions"].append(
                {
                    "name": name,
                    "started_at": started_at,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "outcome": "failed",
                    "details": details,
                    "result": result,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            raise
        else:
            self.report["actions"].append(
                {
                    "name": name,
                    "started_at": started_at,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "outcome": "passed",
                    "details": details,
                    "result": result,
                }
            )

    def finish(self, status: str, limitations: list[str]) -> None:
        self.report["status"] = status
        self.report["exit_status"] = 0 if status == "passed" else 1
        self.report["completed_at"] = utc_now()
        self.report["limitations"] = limitations
        self.report["measured_results"] = {
            "actions_passed": sum(
                action["outcome"] == "passed" for action in self.report["actions"]
            ),
            "actions_failed": sum(
                action["outcome"] == "failed" for action in self.report["actions"]
            ),
            "incidents_exercised": len(self.report["incidents"]),
            "candidate_rejected": self.report["release"].get("candidate_rejected"),
            "restored_release": self.report["release"].get("restored_release"),
            "load": self.report["load"],
        }


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)
