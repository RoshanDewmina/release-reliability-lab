# Local release lab contract

- `GET /health` on the report server returns `{"status":"ok","service":"release-reliability-lab","version":"0.1.0"}`.
- The report server defaults to `127.0.0.1:8116`; `--host` and `--port` explicitly control binding.
- Actual integration targets use 8211 for durable workflows and 8215 for the model lifecycle service.
- Health gates require status `ok`, the exact expected service identity, and exact expected release version before any functional probe passes.
- Reports are immutable run observations once written. Each write uses a temporary file and atomic replacement.
- Fault injection can signal only processes started and registered by the current lab run.
- Fixture and actual-service modes are labeled distinctly; only actual mode supports actual portfolio integration claims.

