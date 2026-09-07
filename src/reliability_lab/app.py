from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from reliability_lab import __version__


def create_app(report_path: Path) -> FastAPI:
    application = FastAPI(title="Release Reliability Lab", version=__version__)
    static_dir = Path(__file__).resolve().parents[2] / "static"
    if not static_dir.exists():
        static_dir = Path(__file__).resolve().parent / "static"
    application.mount("/static", StaticFiles(directory=static_dir), name="static")

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "release-reliability-lab", "version": __version__}

    @application.get("/api/report")
    def report() -> dict[str, object]:
        try:
            content = json.loads(report_path.read_text())
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404, detail="report not found; run the lab first"
            ) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=503, detail="report unavailable") from exc
        if not isinstance(content, dict):
            raise HTTPException(status_code=503, detail="report is not an object")
        return content

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    return application
