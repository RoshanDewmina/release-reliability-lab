from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from reliability_lab.adapters import (
    MODEL_INPUT,
    ProbeFailure,
    durable_adapter,
    model_adapter,
    wait_until_responding,
)
from reliability_lab.config import RunConfig
from reliability_lab.processes import ProcessRegistry, assert_port_free
from reliability_lab.reporting import RunRecorder, git_state


def _python(repository: Path) -> str:
    executable = repository / ".venv" / "bin" / "python"
    if not executable.exists():
        raise RuntimeError(f"dependency environment missing; run uv sync --frozen in {repository}")
    return str(executable)


def _registry_hash(registry: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in registry.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(registry)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class ReliabilityRun:
    def __init__(self, config: RunConfig):
        config.validate()
        self.config = config
        dependencies: dict[str, Any]
        if config.mode == "actual":
            dependencies = {
                "durable-workflows": git_state(config.durable_repo),
                "model-lifecycle-service": git_state(config.model_repo),
            }
        else:
            dependencies = {"fixtures": {"kind": "built-in", "synthetic": True}}
        self.recorder = RunRecorder(config.mode, dependencies)
        self.processes = ProcessRegistry()

    def execute(self) -> dict[str, Any]:
        limitations = [
            "All drills use owned local child processes and isolated temporary state.",
            "Observed timing is host- and load-dependent and does not establish production SLOs.",
        ]
        if self.config.mode == "fixtures":
            limitations.append(
                "Fixture mode is synthetic and is not integration evidence for portfolio services."
            )
        try:
            with tempfile.TemporaryDirectory(prefix="release-reliability-lab-") as temp:
                workspace = Path(temp)
                try:
                    assert_port_free("127.0.0.1", self.config.durable_port)
                    assert_port_free("127.0.0.1", self.config.model_port)
                    self._durable_drill(workspace)
                    self._model_release_drill(workspace)
                finally:
                    self.processes.stop_all()
            self.recorder.finish("passed", limitations)
        except BaseException as exc:
            self.recorder.report["fatal_error"] = f"{type(exc).__name__}: {exc}"
            self.recorder.finish("failed", limitations)
        finally:
            self.processes.stop_all()
        return self.recorder.report

    def _start_fixture(self, name: str, profile: str, port: int, workspace: Path) -> int:
        managed = self.processes.start(
            name,
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
            workspace / "logs" / f"{name}.log",
        )
        return managed.pid

    def _start_durable(self, name: str, workspace: Path, database: Path, *, workers: bool) -> int:
        assert_port_free("127.0.0.1", self.config.durable_port)
        if self.config.mode == "fixtures":
            return self._start_fixture(name, "durable", self.config.durable_port, workspace)
        environment = {
            **os.environ,
            "DW_DB_PATH": str(database),
            "DW_START_WORKERS": "true" if workers else "false",
            "DW_WORKER_COUNT": "2",
        }
        managed = self.processes.start(
            name,
            [
                _python(self.config.durable_repo),
                "-m",
                "uvicorn",
                "durable_workflows.app:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.config.durable_port),
            ],
            self.config.durable_repo,
            environment,
            workspace / "logs" / f"{name}.log",
        )
        return managed.pid

    def _start_model(
        self, name: str, workspace: Path, registry: Path | None, *, candidate: bool = False
    ) -> int:
        assert_port_free("127.0.0.1", self.config.model_port)
        if self.config.mode == "fixtures":
            profile = "wrong-model" if candidate else "model"
            return self._start_fixture(name, profile, self.config.model_port, workspace)
        environment = {**os.environ, "MODEL_REGISTRY": str(registry)}
        managed = self.processes.start(
            name,
            [
                _python(self.config.model_repo),
                "-m",
                "uvicorn",
                "model_lifecycle.api:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.config.model_port),
            ],
            self.config.model_repo,
            environment,
            workspace / "logs" / f"{name}.log",
        )
        return managed.pid

    def _durable_drill(self, workspace: Path) -> None:
        adapter = durable_adapter(self.config.durable_port)
        database = workspace / "durable" / "workflow.db"
        with self.recorder.action("durable_baseline_gate") as result:
            result["pid"] = self._start_durable(
                "durable-baseline", workspace, database, workers=False
            )
            result["startup_http_status"] = wait_until_responding(
                adapter.base_url, self.config.startup_timeout_seconds
            )
            result["gate"] = adapter.gate(self.config.request_timeout_seconds)
            persisted_job = result["gate"]["probe"]["job_id"]

        outage_started = time.perf_counter()
        with self.recorder.action("durable_process_interruption") as result:
            result["signal"] = "SIGKILL"
            result["returncode"] = self.processes.interrupt("durable-baseline", force=True)
            try:
                with httpx.Client(timeout=0.3, trust_env=False) as client:
                    client.get(f"{adapter.base_url}/health")
            except httpx.HTTPError as exc:
                result["outage_observed"] = True
                result["failure"] = type(exc).__name__
            else:
                raise ProbeFailure(
                    "durable service still answered after owned process interruption"
                )

        with self.recorder.action("durable_restart_recovery") as result:
            result["pid"] = self._start_durable(
                "durable-recovered", workspace, database, workers=True
            )
            wait_until_responding(adapter.base_url, self.config.startup_timeout_seconds)
            result["gate"] = adapter.gate(self.config.request_timeout_seconds)
            if self.config.mode == "actual":
                result["persisted_job"] = self._poll_durable(persisted_job)
                if result["persisted_job"]["state"] != "succeeded":
                    raise ProbeFailure("persisted queued job did not succeed after restart")
            result["recovery_ms"] = round((time.perf_counter() - outage_started) * 1000, 3)

        self.recorder.report["incidents"].append(
            {
                "id": "durable-owned-process-interruption",
                "trigger": "SIGKILL sent to the lab-owned durable service process group.",
                "impact": (
                    "Health became unreachable; no unrelated process or persistent state "
                    "was touched."
                ),
                "recovery": (
                    "The same service revision restarted against the same isolated SQLite file."
                    if self.config.mode == "actual"
                    else "The known-good durable fixture process restarted."
                ),
                "observed_recovery_ms": result["recovery_ms"],
                "data_check": "A pre-interruption queued job completed after restart."
                if self.config.mode == "actual"
                else "Fixture mode does not assert persistence across restart.",
            }
        )

        load = self._durable_load()
        self.recorder.report["load"].append(load)
        if load["failed"]:
            raise ProbeFailure(f"durable load probe had {load['failed']} failures")

    def _poll_durable(self, job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + self.config.startup_timeout_seconds
        headers = {"Authorization": "Bearer demo-alpha-token"}
        with httpx.Client(timeout=self.config.request_timeout_seconds, trust_env=False) as client:
            while time.monotonic() < deadline:
                response = client.get(
                    f"http://127.0.0.1:{self.config.durable_port}/jobs/{job_id}", headers=headers
                )
                if response.status_code == 200 and response.json()["state"] in {
                    "succeeded",
                    "failed",
                    "cancelled",
                }:
                    return response.json()
                time.sleep(0.05)
        raise ProbeFailure("durable persisted job did not reach a terminal state")

    def _durable_load(self) -> dict[str, Any]:
        base_url = f"http://127.0.0.1:{self.config.durable_port}"
        count = self.config.load_requests
        started = time.perf_counter()
        failures: list[str] = []
        latencies: list[float] = []

        def request(index: int) -> tuple[str, float]:
            began = time.perf_counter()
            with httpx.Client(
                timeout=self.config.request_timeout_seconds, trust_env=False
            ) as client:
                response = client.post(
                    f"{base_url}/jobs",
                    headers={
                        "Authorization": "Bearer demo-alpha-token",
                        "Idempotency-Key": f"load-{uuid.uuid4()}-{index}",
                    },
                    json={"kind": "data_import", "records": [{"value": index}, {"value": 1}]},
                )
                if response.status_code != 201:
                    raise ProbeFailure(f"HTTP {response.status_code}")
                return response.json()["id"], (time.perf_counter() - began) * 1000

        job_ids = []
        with ThreadPoolExecutor(max_workers=min(8, count)) as pool:
            futures = [pool.submit(request, index) for index in range(count)]
            for future in futures:
                try:
                    job_id, latency = future.result()
                    job_ids.append(job_id)
                    latencies.append(latency)
                except Exception as exc:
                    failures.append(f"{type(exc).__name__}: {exc}")
        for job_id in job_ids:
            try:
                if self._poll_durable(job_id)["state"] != "succeeded":
                    failures.append(f"job {job_id} did not succeed")
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
        elapsed = time.perf_counter() - started
        ordered = sorted(latencies)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)] if ordered else None
        return {
            "service": "durable-workflows",
            "requests": count,
            "concurrency": min(8, count),
            "succeeded": count - len(failures),
            "failed": len(failures),
            "failure_examples": failures[:3],
            "elapsed_seconds": round(elapsed, 6),
            "requests_per_second": round(count / elapsed, 3),
            "submission_latency_p95_ms": round(p95, 3) if p95 is not None else None,
        }

    def _model_release_drill(self, workspace: Path) -> None:
        adapter = model_adapter(self.config.model_port)
        known_registry = None
        candidate_registry = None
        known_hash = "fixture"
        if self.config.mode == "actual":
            source = self.config.model_repo / "src" / "model_lifecycle" / "bundled_registry"
            known_registry = workspace / "model" / "known-good"
            candidate_registry = workspace / "model" / "candidate"
            shutil.copytree(source, known_registry)
            shutil.copytree(source, candidate_registry)
            registry_file = candidate_registry / "registry.json"
            registry = json.loads(registry_file.read_text())
            registry["active_version"] = "broken-candidate-v2"
            registry_file.write_text(json.dumps(registry, indent=2) + "\n")
            known_hash = _registry_hash(known_registry)

        with self.recorder.action("model_known_release_gate") as result:
            result["pid"] = self._start_model("model-known", workspace, known_registry)
            wait_until_responding(adapter.base_url, self.config.startup_timeout_seconds)
            result["gate"] = adapter.gate(self.config.request_timeout_seconds)
            result["registry_sha256"] = known_hash

        load = self._model_load()
        self.recorder.report["load"].append(load)
        if load["failed"]:
            raise ProbeFailure(f"model load probe had {load['failed']} failures")

        self.processes.interrupt("model-known")
        with self.recorder.action("model_candidate_fail_closed") as result:
            result["pid"] = self._start_model(
                "model-candidate", workspace, candidate_registry, candidate=True
            )
            result["startup_http_status"] = wait_until_responding(
                adapter.base_url, self.config.startup_timeout_seconds
            )
            try:
                adapter.gate(self.config.request_timeout_seconds)
            except ProbeFailure as exc:
                result["candidate_rejected"] = True
                result["reason"] = str(exc)
            else:
                raise ProbeFailure("invalid candidate passed the release gate")
        self.processes.interrupt("model-candidate")

        rollback_started = time.perf_counter()
        with self.recorder.action("model_last_known_good_rollback") as result:
            result["pid"] = self._start_model("model-rollback", workspace, known_registry)
            wait_until_responding(adapter.base_url, self.config.startup_timeout_seconds)
            result["gate"] = adapter.gate(self.config.request_timeout_seconds)
            result["registry_sha256"] = (
                _registry_hash(known_registry) if known_registry else "fixture"
            )
            result["rollback_ms"] = round((time.perf_counter() - rollback_started) * 1000, 3)
            if result["registry_sha256"] != known_hash:
                raise ProbeFailure("last-known-good registry changed during release drill")

        self.recorder.report["release"] = {
            "candidate": "broken-candidate-v2" if self.config.mode == "actual" else "wrong-version",
            "candidate_rejected": True,
            "restored_release": "wine-logreg-v1",
            "restored_registry_sha256": known_hash,
            "rollback_ms": result["rollback_ms"],
            "meaning": (
                "A real model service was started against the copied last-known-good registry "
                "and passed health plus prediction after the candidate process failed its gate."
            )
            if self.config.mode == "actual"
            else "Synthetic fixture rollback; not actual-service evidence.",
        }
        self.recorder.report["incidents"].append(
            {
                "id": "model-candidate-release-rejected",
                "trigger": (
                    "Candidate service started with a registry pointing to a missing "
                    "release artifact."
                )
                if self.config.mode == "actual"
                else "Fixture returned an unexpected release version.",
                "impact": "Health or version contract failed; traffic gate remained closed.",
                "recovery": (
                    "Candidate stopped; actual last-known-good artifact registry restored and "
                    "prediction verified."
                )
                if self.config.mode == "actual"
                else "Known-good fixture restored.",
                "observed_recovery_ms": result["rollback_ms"],
            }
        )

    def _model_load(self) -> dict[str, Any]:
        base_url = f"http://127.0.0.1:{self.config.model_port}"
        count = self.config.load_requests
        started = time.perf_counter()
        latencies: list[float] = []
        failures: list[str] = []

        def request() -> float:
            began = time.perf_counter()
            with httpx.Client(
                timeout=self.config.request_timeout_seconds, trust_env=False
            ) as client:
                response = client.post(f"{base_url}/predict", json=MODEL_INPUT)
                if (
                    response.status_code != 200
                    or response.json().get("model_version") != "wine-logreg-v1"
                ):
                    raise ProbeFailure(f"prediction contract failed: HTTP {response.status_code}")
            return (time.perf_counter() - began) * 1000

        with ThreadPoolExecutor(max_workers=min(8, count)) as pool:
            futures = [pool.submit(request) for _ in range(count)]
            for future in futures:
                try:
                    latencies.append(future.result())
                except Exception as exc:
                    failures.append(f"{type(exc).__name__}: {exc}")
        elapsed = time.perf_counter() - started
        ordered = sorted(latencies)
        p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)] if ordered else None
        return {
            "service": "model-lifecycle-service",
            "requests": count,
            "concurrency": min(8, count),
            "succeeded": count - len(failures),
            "failed": len(failures),
            "failure_examples": failures[:3],
            "elapsed_seconds": round(elapsed, 6),
            "requests_per_second": round(count / elapsed, 3),
            "latency_p95_ms": round(p95, 3) if p95 is not None else None,
        }
