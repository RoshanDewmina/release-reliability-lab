FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY static ./static
RUN uv sync --frozen --no-dev
COPY evidence/latest-run.json ./evidence/latest-run.json
EXPOSE 8116
CMD ["reliability-lab", "serve", "--host", "0.0.0.0", "--port", "8116", "--report", "evidence/latest-run.json"]

