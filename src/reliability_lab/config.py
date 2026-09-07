from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PORTFOLIO_ROOT = Path("/Users/roshansilva/Documents/GitHub/career-portfolio")
DEFAULT_DURABLE_REPO = PORTFOLIO_ROOT / "durable-workflows"
DEFAULT_MODEL_REPO = PORTFOLIO_ROOT / "model-lifecycle-service"


@dataclass(frozen=True, slots=True)
class RunConfig:
    mode: str
    durable_repo: Path = DEFAULT_DURABLE_REPO
    model_repo: Path = DEFAULT_MODEL_REPO
    durable_port: int = 8211
    model_port: int = 8215
    load_requests: int = 12
    request_timeout_seconds: float = 2.0
    startup_timeout_seconds: float = 30.0

    def validate(self) -> None:
        if self.mode not in {"actual", "fixtures"}:
            raise ValueError("mode must be actual or fixtures")
        if self.durable_port == self.model_port:
            raise ValueError("service ports must be distinct")
        if any(port < 1024 or port > 65535 for port in (self.durable_port, self.model_port)):
            raise ValueError("ports must be between 1024 and 65535")
        if not 1 <= self.load_requests <= 200:
            raise ValueError("load requests must be between 1 and 200")
        if self.request_timeout_seconds <= 0 or self.startup_timeout_seconds <= 0:
            raise ValueError("timeouts must be positive")
        if self.mode == "actual":
            for repository in (self.durable_repo, self.model_repo):
                if not (repository / ".git").exists():
                    raise ValueError(f"actual dependency repository unavailable: {repository}")
