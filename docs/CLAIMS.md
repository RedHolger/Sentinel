# Current Claims & Evidence Ledger

All performance, memory, and hardware claims must link directly to an artifact and verifiable execution command.

| Claim | Target / Scope | Current Status | Supporting Evidence |
|---|---|---|---|
| Offline CLI scaffold invocation | Local Host CPU | VALIDATED | Installed `adi-portfolio demo` execution; isolated release verification |
| Historical baseline digest integrity | Repository Baseline | VALIDATED | `scripts/verify_historical_hashes.py` verifying `handoff/00/artifact-manifest.sha256` |
| Offline deterministic decision path | Local Host CPU | NOT_IMPLEMENTED | Planned for Stage 07/08; CLI is baseline scaffold only |
| Zero unhandled contradictions | Local Host CPU | NOT_IMPLEMENTED | Planned for Stage 07; verifier not yet implemented |
| Pipeline latency benchmark | Local Host CPU | NOT_IMPLEMENTED | `adi-portfolio benchmark` returns status `NOT_IMPLEMENTED`, exit 1 |
| Integration evaluation suite | Local Host CPU | NOT_IMPLEMENTED | `adi-portfolio evaluate` returns status `NOT_IMPLEMENTED`, exit 1 |
| Embedded hardware acceleration | MAX78000 / MAX78002 / SHARC | UNVERIFIED / BLOCKED | Hardware unattached; target runbook unexecuted |
| SRAM footprint under memory budget | Target Architecture | PLANNED | Analytical SRAM calculations planned for Stage 03 |
| Quantization numerical parity | Host CPU | PLANNED | Awaiting Stage 04 quantization benchmark |
| Sensor signal retrieval quality | Host CPU | PLANNED | Awaiting Stage 06 catalog evaluation |

## Rules for Evidence

1. **Host $\ne$ Target:** A result executed on macOS/x86 host CPU is never classified as target embedded hardware execution.
2. **Analytical $\ne$ Measured:** Calculated memory formulas are classified as `MODELLED`, not `MEASURED`.
3. **No Phantom Offload:** Registering an execution provider or setting a boolean flag does not prove hardware offload.
