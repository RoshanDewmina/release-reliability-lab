from __future__ import annotations

import argparse
import json
from pathlib import Path

import uvicorn

from reliability_lab.app import create_app
from reliability_lab.config import DEFAULT_DURABLE_REPO, DEFAULT_MODEL_REPO, RunConfig
from reliability_lab.orchestrator import ReliabilityRun
from reliability_lab.reporting import write_report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="reliability-lab")
    commands = result.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="execute isolated gates and failure drills")
    run.add_argument("--mode", choices=("actual", "fixtures"), required=True)
    run.add_argument("--durable-repo", type=Path, default=DEFAULT_DURABLE_REPO)
    run.add_argument("--model-repo", type=Path, default=DEFAULT_MODEL_REPO)
    run.add_argument("--durable-port", type=int, default=8211)
    run.add_argument("--model-port", type=int, default=8215)
    run.add_argument("--load", type=int, default=12)
    run.add_argument("--output", type=Path, required=True)

    serve = commands.add_parser("serve", help="serve a read-only run report")
    serve.add_argument("--report", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8116)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "serve":
        uvicorn.run(create_app(args.report.resolve()), host=args.host, port=args.port)
        return
    config = RunConfig(
        mode=args.mode,
        durable_repo=args.durable_repo.resolve(),
        model_repo=args.model_repo.resolve(),
        durable_port=args.durable_port,
        model_port=args.model_port,
        load_requests=args.load,
    )
    report = ReliabilityRun(config).execute()
    write_report(args.output, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "mode": report["mode"],
                "run_id": report["run_id"],
                "output": str(args.output),
                "actions": len(report["actions"]),
                "incidents": len(report["incidents"]),
            },
            indent=2,
        )
    )
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
