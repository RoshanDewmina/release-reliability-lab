# Interview guide

## Two-minute explanation

Blackbox provides repeatable release evidence for two independently runnable services. It treats health as a contract, probes a useful API path, adds small bounded load, and records every action. It controls only child processes it started and stores all mutable service state in a temporary directory. The durable drill kills a process with a queued request and proves that the restarted service completes it from the same database. The model drill starts a broken candidate, refuses promotion when health fails, and restores a verified last-known-good artifact registry.

## Decisions to defend

**Why black-box probes?** They catch packaging, startup, configuration, routing, validation, and serialization failures that an in-process test can miss. Unit tests remain faster for edge cases.

**Why exact service and version checks?** A generic 200 can come from the wrong process or release. The gate requires the expected identity and version before the API probe runs.

**Why copy registries?** The lab must not mutate the model repository or its bundled release. Separate temporary known-good and candidate directories make the failure isolated and cleanup deterministic.

**Why store owned names instead of accepting PIDs?** A raw PID interface invites signaling unrelated processes. The registry exposes only names returned by its own start operation and uses the exact child process group.

**What does rollback prove?** A failing actual candidate configuration is removed and the actual service is started against the unchanged last-known-good model artifact, then health and prediction pass. It does not prove distributed traffic management.

## Failure timeline

1. Verify the baseline actual release.
2. Stop only the owned baseline process.
3. Start the candidate with its isolated broken registry.
4. Observe HTTP 503 and keep the release gate closed.
5. Stop the candidate and start the known release against its recorded registry hash.
6. Verify health, version, prediction schema, and probabilities.
7. Record rollback time, action telemetry, incident text, dependency revisions, and limits.

## Hands-on exercises

1. Run fixture mode, inspect the JSON, and identify why it cannot count as actual integration evidence.
2. Run `make demo`, find the durable outage and persisted-job recovery actions, and explain their timing.
3. Change the expected model version in an adapter and confirm the gate fails closed.
4. Read the process ownership test, then explain why a global `pkill uvicorn` would be unsafe.
5. Compare two benchmark receipts and explain why the rates are observations rather than targets.
6. Sketch an extension for a third service using `ServiceAdapter` without adding new process-kill authority.

## Mastery checklist

- Explain readiness versus liveness and why the gate uses readiness behavior.
- Explain release rollback versus process restart.
- Reproduce the failed candidate and identify the exact contract failure.
- Describe one failure the lab does not simulate.
- Review and approve resume wording personally.

Personal mastery status: **pending**.

