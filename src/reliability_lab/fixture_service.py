from __future__ import annotations

import argparse
import os
import uuid

import uvicorn
from fastapi import FastAPI, Header, HTTPException

from reliability_lab.adapters import MODEL_INPUT


def create_fixture(profile: str) -> FastAPI:
    application = FastAPI(title=f"Reliability fixture: {profile}")
    jobs: dict[str, dict[str, object]] = {}

    @application.get("/health")
    def health():
        if profile == "broken":
            raise HTTPException(status_code=503, detail="controlled fixture failure")
        if profile == "durable":
            return {"status": "ok", "service": "durable-workflows", "version": "0.1.0"}
        version = "wrong-version" if profile == "wrong-model" else "wine-logreg-v1"
        return {"status": "ok", "service": "model-lifecycle-service", "version": version}

    @application.post("/jobs", status_code=201)
    def create_job(authorization: str | None = Header(default=None)):
        if profile != "durable":
            raise HTTPException(status_code=404)
        if authorization != "Bearer demo-alpha-token":
            raise HTTPException(status_code=401)
        job_id = str(uuid.uuid4())
        jobs[job_id] = {
            "id": job_id,
            "state": "succeeded",
            "record_count": 2,
            "result": {"accepted_count": 2, "value_sum": 5},
        }
        return jobs[job_id]

    @application.get("/jobs/{job_id}")
    def get_job(job_id: str, authorization: str | None = Header(default=None)):
        if authorization == "Bearer demo-beta-token" or job_id not in jobs:
            raise HTTPException(status_code=404)
        if authorization != "Bearer demo-alpha-token":
            raise HTTPException(status_code=401)
        return jobs[job_id]

    @application.post("/predict")
    def predict(payload: dict[str, float]):
        if profile not in {"model", "wrong-model"}:
            raise HTTPException(status_code=404)
        if set(payload) != set(MODEL_INPUT):
            raise HTTPException(status_code=422)
        version = "wrong-version" if profile == "wrong-model" else "wine-logreg-v1"
        return {
            "prediction": 0,
            "class_name": "cultivar_1",
            "probabilities": {"cultivar_1": 0.8, "cultivar_2": 0.1, "cultivar_3": 0.1},
            "model_version": version,
        }

    return application


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("durable", "model", "wrong-model", "broken"))
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    os.environ.pop("HTTP_PROXY", None)
    uvicorn.run(create_fixture(args.profile), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
