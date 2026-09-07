from __future__ import annotations

import statistics
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

import httpx


class ProbeFailure(RuntimeError):
    pass


MODEL_INPUT = {
    "alcohol": 13.2,
    "malic_acid": 1.8,
    "ash": 2.4,
    "alcalinity_of_ash": 18.0,
    "magnesium": 100.0,
    "total_phenols": 2.6,
    "flavanoids": 2.7,
    "nonflavanoid_phenols": 0.3,
    "proanthocyanins": 1.8,
    "color_intensity": 5.0,
    "hue": 1.0,
    "od280_od315_of_diluted_wines": 3.0,
    "proline": 900.0,
}


@dataclass(frozen=True, slots=True)
class ServiceAdapter:
    name: str
    base_url: str
    expected_service: str
    expected_version: str
    probe: Callable[[httpx.Client], dict[str, Any]]

    def gate(self, timeout_seconds: float) -> dict[str, Any]:
        timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds)
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            try:
                response = client.get(f"{self.base_url}/health")
            except httpx.HTTPError as exc:
                raise ProbeFailure(f"health request failed: {type(exc).__name__}: {exc}") from exc
            if response.status_code != 200:
                raise ProbeFailure(
                    f"health returned HTTP {response.status_code}: {response.text[:200]}"
                )
            try:
                health = response.json()
            except ValueError as exc:
                raise ProbeFailure("health did not return JSON") from exc
            required = {
                "status": "ok",
                "service": self.expected_service,
                "version": self.expected_version,
            }
            if not isinstance(health, dict) or any(
                health.get(key) != value for key, value in required.items()
            ):
                raise ProbeFailure(f"health contract mismatch: expected {required}, got {health}")
            probe_result = self.probe(client)
        return {"health": health, "probe": probe_result}


def durable_adapter(port: int, expected_version: str = "0.1.0") -> ServiceAdapter:
    base_url = f"http://127.0.0.1:{port}"

    def probe(client: httpx.Client) -> dict[str, Any]:
        key = f"release-gate-{uuid.uuid4()}"
        response = client.post(
            f"{base_url}/jobs",
            headers={
                "Authorization": "Bearer demo-alpha-token",
                "Idempotency-Key": key,
            },
            json={"kind": "data_import", "records": [{"value": 2}, {"value": 3}]},
        )
        if response.status_code != 201:
            raise ProbeFailure(
                f"durable create returned HTTP {response.status_code}: {response.text[:200]}"
            )
        body = response.json()
        if (
            body.get("state") not in {"queued", "running", "succeeded"}
            or body.get("record_count") != 2
        ):
            raise ProbeFailure(f"durable create contract mismatch: {body}")
        job_id = body.get("id")
        detail = client.get(
            f"{base_url}/jobs/{job_id}",
            headers={"Authorization": "Bearer demo-alpha-token"},
        )
        if detail.status_code != 200 or detail.json().get("id") != job_id:
            raise ProbeFailure("durable owner-scoped read contract failed")
        denied = client.get(
            f"{base_url}/jobs/{job_id}",
            headers={"Authorization": "Bearer demo-beta-token"},
        )
        if denied.status_code != 404:
            raise ProbeFailure("durable cross-owner denial contract failed")
        return {"job_id": job_id, "initial_state": body["state"], "cross_owner_status": 404}

    return ServiceAdapter(
        name="durable",
        base_url=base_url,
        expected_service="durable-workflows",
        expected_version=expected_version,
        probe=probe,
    )


def model_adapter(port: int, expected_version: str = "wine-logreg-v1") -> ServiceAdapter:
    base_url = f"http://127.0.0.1:{port}"

    def probe(client: httpx.Client) -> dict[str, Any]:
        response = client.post(f"{base_url}/predict", json=MODEL_INPUT)
        if response.status_code != 200:
            raise ProbeFailure(
                f"model predict returned HTTP {response.status_code}: {response.text[:200]}"
            )
        body = response.json()
        if body.get("model_version") != expected_version:
            raise ProbeFailure(f"predict model version mismatch: {body.get('model_version')}")
        if not isinstance(body.get("prediction"), int) or not isinstance(
            body.get("probabilities"), dict
        ):
            raise ProbeFailure(f"model predict schema mismatch: {body}")
        probability_sum = sum(body["probabilities"].values())
        if abs(probability_sum - 1.0) > 1e-6:
            raise ProbeFailure(f"model probabilities did not sum to one: {probability_sum}")
        return {
            "model_version": body["model_version"],
            "prediction": body["prediction"],
            "probability_sum": probability_sum,
        }

    return ServiceAdapter(
        name="model",
        base_url=base_url,
        expected_service="model-lifecycle-service",
        expected_version=expected_version,
        probe=probe,
    )


def wait_until_responding(base_url: str, timeout_seconds: float) -> int:
    deadline = time.monotonic() + timeout_seconds
    timeout = httpx.Timeout(0.3, connect=0.3)
    with httpx.Client(timeout=timeout, trust_env=False) as client:
        while time.monotonic() < deadline:
            try:
                return client.get(f"{base_url}/health").status_code
            except httpx.HTTPError:
                time.sleep(0.05)
    raise ProbeFailure(f"service did not respond within {timeout_seconds:.1f}s: {base_url}")


def assert_unavailable(base_url: str) -> str:
    try:
        with httpx.Client(timeout=0.3, trust_env=False) as client:
            response = client.get(f"{base_url}/health")
    except httpx.HTTPError as exc:
        return type(exc).__name__
    raise ProbeFailure(f"expected outage, received HTTP {response.status_code}")


def poll_durable_job(port: int, job_id: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    url = f"http://127.0.0.1:{port}/jobs/{job_id}"
    headers = {"Authorization": "Bearer demo-alpha-token"}
    with httpx.Client(timeout=1, trust_env=False) as client:
        while time.monotonic() < deadline:
            response = client.get(url, headers=headers)
            if response.status_code == 200 and response.json().get("state") in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                return response.json()
            time.sleep(0.04)
    raise ProbeFailure(f"durable job {job_id} did not reach terminal state")


def run_load(
    name: str,
    count: int,
    concurrency: int,
    operation: Callable[[int], None],
) -> dict[str, Any]:
    latencies: list[float] = []
    failures: list[str] = []
    started = time.perf_counter()

    def timed(index: int) -> float:
        call_started = time.perf_counter()
        operation(index)
        return (time.perf_counter() - call_started) * 1000

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(timed, index) for index in range(count)]
        for future in as_completed(futures):
            try:
                latencies.append(future.result())
            except Exception as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
    elapsed = time.perf_counter() - started
    sorted_latencies = sorted(latencies)
    p95_index = max(0, min(len(sorted_latencies) - 1, int(len(sorted_latencies) * 0.95) - 1))
    return {
        "service": name,
        "requests": count,
        "concurrency": concurrency,
        "successes": len(latencies),
        "failures": len(failures),
        "failure_samples": failures[:3],
        "elapsed_seconds": round(elapsed, 6),
        "requests_per_second": round(count / elapsed, 3),
        "latency_ms_median": round(statistics.median(latencies), 3) if latencies else None,
        "latency_ms_p95": round(sorted_latencies[p95_index], 3) if latencies else None,
    }
