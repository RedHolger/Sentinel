# ADI Portfolio Release Guide

## 1. Baseline Offline Verification

The portfolio is structured for offline, self-contained packaging verification:

```sh
# Step 1: Explicit build and environment bootstrap
sh scripts/bootstrap_portfolio.sh

# Step 2: Run installed entry points (baseline scaffold)
.release/venv/bin/adi-portfolio demo        # Baseline CLI scaffold (exits 0)
.release/venv/bin/adi-portfolio benchmark   # Returns NOT_IMPLEMENTED (exits 1)
.release/venv/bin/adi-portfolio evaluate    # Returns NOT_IMPLEMENTED (exits 1)
```

### Network Isolation & Offline Operation Scope
- **Offline Build & Packaging:** Dependencies and wheel builds in `scripts/bootstrap_portfolio.sh` and `scripts/verify_portfolio_release.py` use local files and pre-installed toolchains (`uv build --offline`, `uv pip install --offline`). Runtime entry points do not require or initiate external network weight downloads.
- **Limited Python Socket Guard:** In release verification (`scripts/verify_portfolio_release.py`), an in-process monkeypatch (`socket.socket.connect = deny`, `socket.create_connection = deny`) is installed during the imported-`main` test to verify that internal Python code does not call standard socket connection methods.
- **No OS-Level Network Denial:** Direct CLI execution (`.release/venv/bin/adi-portfolio` or `env/bin/adi-portfolio`) and external subprocesses have **no OS-level network-denial guarantee**. The test harness does not enforce kernel-level network namespaces, packet filtering, or an operating-system firewall sandbox.

## 2. Capability & Activation Matrix

| Capability | Current Status | Activation Command / Requirements |
|---|---|---|
| Offline CLI Scaffold | Validated | `.release/venv/bin/adi-portfolio demo` |
| Deterministic Decision Engine | PLANNED / NOT IMPLEMENTED | Requires Stage 07/08 verifier implementation |
| Pipeline Latency Benchmark | PLANNED / NOT IMPLEMENTED | Requires Stage 04/08 benchmarking harness; `.release/venv/bin/adi-portfolio benchmark` returns `NOT_IMPLEMENTED` (exit 1) |
| Internal Regression Evaluation | PLANNED / NOT IMPLEMENTED | Requires Stage 07/09 evaluation suite; `.release/venv/bin/adi-portfolio evaluate` returns `NOT_IMPLEMENTED` (exit 1) |
| Embedded Hardware Execution | BLOCKED / UNVERIFIED | Requires physical board connection, firmware build, and profiler telemetry |
| Quantized Integer Acceleration | PLANNED | Complete Stage 04 quantization benchmark |
| Independent Generalization | PLANNED | Curate and freeze independent held-out evaluation dataset |

## 3. Rollback & Migration Protocol

- Disposable environments reside in `.release/venv`. To roll back, delete `.release/` and rebuild.
- Preserved historical evidence in `handoff/00/` must never be modified or overwritten.
