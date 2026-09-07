.PHONY: setup test lint demo benchmark report

setup:
	uv sync --frozen

test:
	uv run ruff check .
	uv run pytest

lint:
	uv run ruff check .

demo:
	uv run reliability-lab run --mode actual --durable-port 8211 --model-port 8215 --load 12 --output evidence/latest-run.json

benchmark:
	uv run reliability-lab run --mode actual --durable-port 8211 --model-port 8215 --load 40 --output evidence/benchmark.json

report:
	uv run reliability-lab serve --report evidence/latest-run.json

