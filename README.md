# Sentinel

**Vibration Analysis & Measurement Validation Engine**

[![Tests](https://img.shields.io/badge/tests-438%20passed-brightgreen.svg)]()
[![Contracts](https://img.shields.io/badge/contracts-32%2F32%20passed-blue.svg)]()
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue.svg)]()
[![Status](https://img.shields.io/badge/status-software%20prototype-orange.svg)]()

Sentinel is an independent software prototype for vibration signal analysis, automated data-integrity verification, and deterministic measurement admission gating. Designed for edge condition-based monitoring (CbM) environments, Sentinel couples rigorous JSON Schema (Draft 2020-12) contract validation with statistical feature extraction and fail-closed decision logic.

---

## 1. What Problem Does This Solve?

### Vibration Analysis in Plain Language
Industrial machinery—such as electric induction motors, drive pumps, gearboxes, and compressors—vibrates with characteristic periodic patterns during normal operation. As mechanical components wear down (e.g., spalling on bearing raceways, rotor unbalance, shaft misalignment), the vibration waveform shifts, exhibiting elevated amplitude peaks, high-frequency transients, and abnormal statistical dispersion. By monitoring time-series acceleration data through statistical indicators (such as Root Mean Square, Peak-to-Peak, Crest Factor, and Variance), emerging mechanical anomalies can be flagged before catastrophic equipment failure occurs.

### Measurement Validation: Preventing Garbage-In, Garbage-Out
In condition monitoring, anomaly detection algorithms frequently fail not because the statistical mathematics are invalid, but because the underlying measurement data is corrupted, misconfigured, or scientifically untrusted. Real-world sensor pipelines face severe failure modes:
- **Transducer Rail Saturation & Clipping**: Accelerometers driven past their full-scale range clip, producing artificial flat-topped harmonics that mimic bearing defects.
- **Unverified Physical Calibration**: Raw ADC counts or uncalibrated millivolt outputs mistakenly ingested as acceleration without verified sensitivity factors ($S_v$) or unit validation (`g` vs. $\text{m/s}^2$).
- **Missing or Inaccurate Sampling Rates**: FFTs and time-domain windowing computed on unknown or misstated sampling frequencies ($f_s$).
- **Unauthenticated File Provenance**: Analysis conducted on unversioned, tampered, or mismatched container files.

Sentinel addresses this by placing a deterministic **Measurement Admission Gate** in front of the analytical runtime. Before any vibration recording is admitted for feature extraction or anomaly classification, its binary file integrity, transducer calibration chain, and physical signal bounds must be cryptographically bound and validated.

---

## 2. Package Architecture

Sentinel is organized into three decoupled, version-controlled Python packages:

```text
Sentinel/
├── adi-common/          # Canonical contracts, versioned schemas, and admission validators
│   ├── schemas/         # Draft 2020-12 JSON Schemas (recordings, decisions, calibration)
│   ├── src/adi_common/  # Schema validation engines, evidence rules, and contract guards
│   └── tests/           # Contract, schema, and admission gate test suites (157 tests)
│
├── adi-runtime/         # Statistical feature extraction & threshold evaluation runtime
│   ├── src/adi_runtime/ # RMS, Crest Factor, multi-channel windowing, threshold logic
│   └── tests/           # Numerical parity, analytical reference, and edge-case tests (61 tests)
│
├── adi-app/             # Application CLI, exploratory inspection, & scenario evaluation
│   ├── src/adi_app/     # CLI dispatcher, MATLAB Level 5 inspector, admission simulator
│   └── tests/           # CLI integration, ingestion, and scenario test suites (220 tests)
│
├── docs/                # Architecture specifications and admission protocols
│   ├── ARCHITECTURE.md  # System components, interface boundaries, and data flow
│   ├── CLAIMS.md        # Explicit verification claims ledger
│   ├── RELEASE.md       # Packaging, offline isolation, and deployment guide
│   └── edge-sentinel/   # Stage specifications, calibration protocol, and admission specs
│
├── scripts/             # Isolated build, hash verification, and release tooling
│   ├── bootstrap_portfolio.sh        # Hermetic wheel build and local venv setup
│   ├── verify_portfolio_release.py   # Isolated relocated wheel installation test
│   ├── verify_historical_hashes.py   # Cryptographic digest verification
│   └── verify_stage11_schema.py      # 32-check schema & semantic admission suite
│
├── handoff/00/          # Pinned historical evidence digests and risk registers
└── Makefile             # Developer automation commands
```

### Module Responsibilities

1. **`adi-common`**:
   - Single source of truth for all data contracts.
   - Embedded JSON schemas:
     - `vibration_recording.schema.v1.json`: Time-series sensor payload metadata and arrays.
     - `anomaly_decision.schema.v1.json`: Deterministic inference outcome and attribution report.
     - `exploratory_source_inspection.schema.v1.json`: Byte-level source file inspection findings.
     - `calibration_measurement_evidence.schema.v1.json`: Five-state evidence ledger (Draft 2020-12).
     - `measurement_admission_decision.schema.v1.json`: Formal admission gate pass/fail records.
   - Enforces fail-closed validation on empty registries, uncalibrated inputs, and boolean type-aliasing.

2. **`adi-runtime`**:
   - Computes deterministic time-domain features across multi-channel vibration windows:
     - **Mean & Variance**: Baseline offset and power distribution.
     - **Peak-to-Peak ($V_{pp}$)**: Waveform envelope extremes ($\max(x) - \min(x)$).
     - **Root Mean Square (RMS)**: Continuous vibration energy ($\sqrt{\frac{1}{N}\sum x_i^2}$).
     - **Crest Factor ($C_f$)**: Ratio of peak absolute acceleration to RMS ($\frac{|x|_{\max}}{\text{RMS}}$), highly sensitive to spalling impacts.
   - Evaluates statistical thresholds using a ternary decision model: `NORMAL`, `ANOMALOUS`, and `ABSTAIN` (when signals clip, envelopes mismatch, or metadata is out of bounds).

3. **`adi-app`**:
   - Provides the public CLI tool (`adi-portfolio`).
   - Implements bounded exploratory parsing for uncompressed Level 5 MATLAB source files (`.mat`), extracting raw matrix headers and elements without executing foreign code.
   - Implements multi-scenario batch evaluation and simulated measurement admission.

---

## 3. Data Integrity & Admission Gating

### Source-Byte Binding
To prevent data substitution attacks and silent file corruption, Sentinel enforces **source-byte binding**:
1. When a raw binary container (e.g., Level 5 `.mat` or CSV recording) is inspected, Sentinel computes an immutable SHA-256 digest directly from the raw byte stream on disk.
2. The byte digest is embedded directly into the `ExploratorySourceInspectionV1` report along with variable offsets, element counts, and byte-length accounting.
3. Downstream admission engines verify that the recorded digest matches the physical file before allowing features to be computed.

### Signal Quality Checks
During pre-admission inspection, signals undergo automated physical boundary checks:
- **Rail Saturation / Clipping**: Detects consecutive flat-top rail samples ($\ge 3$ repeated extrema) or clipped proportions ($>0.01\%$), immediately blocking admission under `BLOCKED_SIGNAL_INTEGRITY`.
- **Sampling Frequency**: Rejects recordings with non-positive or unstated native sampling frequencies under `BLOCKED_UNKNOWN_METADATA`.
- **Transducer Sensitivity & Range**: Requires primary calibration certificates (Tier 1/2 evidence) verifying sensitivity ($S_v > 0$) and full-scale range ($g_{\text{fs}} > 0$).
- **Legal & Provenance Clearance**: Evaluates commercial license grants and specimen distinctness to block data leakage across evaluation partitions.

### Deterministic Outcomes
Sentinel uses strict, mutually exclusive enumerations for all decisions:

| Category | Enumerated Outcomes |
|---|---|
| **Measurement Admission** | `ADMITTED_FOR_MEASUREMENT`, `BLOCKED_UNSUPPORTED_FORMAT`, `BLOCKED_SIGNAL_INTEGRITY`, `BLOCKED_USAGE_RESTRICTIONS`, `BLOCKED_PROVENANCE`, `BLOCKED_UNKNOWN_METADATA`, `BLOCKED_CONFLICTING_EVIDENCE`, `BLOCKED_CALIBRATION` |
| **Runtime Detection** | `NORMAL` (within envelope), `ANOMALOUS` (threshold exceeded), `ABSTAIN` (saturation, envelope mismatch, or incomplete evidence) |

---

## 4. System Boundaries & Implementation Status

To maintain rigorous scientific and engineering transparency, Sentinel clearly defines what is implemented versus proposed:

| System Layer | Current Status | Description |
|---|---|---|
| **Schema & Contract System** | **Implemented & Verified** | 5 Draft 2020-12 schemas, fail-closed validators, and semantic rules (`adi-common`). |
| **Signal Processing Runtime** | **Implemented & Verified** | Windowing, RMS, Peak-to-Peak, Crest Factor, variance, and ternary thresholding (`adi-runtime`). |
| **CLI & Inspection Engine** | **Implemented & Verified** | Offline CLI, scenario evaluator, Level 5 MATLAB inspector, and admission gate (`adi-app`). |
| **Offline Packaging Pipeline** | **Implemented & Verified** | Hermetic wheel builds, isolated relocated virtual environment testing (`scripts/`). |
| **Pico + ADXL345 Test Bench** | **Proposed Experiment** | Proposed Raspberry Pi Pico firmware and ADXL345 accelerometer for motor test bench acquisition. |
| **UL Laboratory Validation** | **Future Work** | Third-party accredited laboratory testing for environmental and vibrational calibration. |
| **Physical Hardware Deployment** | **Not Implemented** | Physical microcontroller/DSP deployment, embedded device drivers, and on-chip execution. |
| **Real-Machine Fault Accuracy** | **Not Demonstrated** | Real-world motor fault accuracy, classification curves, and predictive maintenance claims. |

> [!IMPORTANT]
> **Prototype Scope Notice:**  
> Sentinel is an independent software prototype demonstrating data validation, contract enforcement, and signal processing algorithms. It **does not claim production readiness**, does not claim real-time operational predictive maintenance, and has not yet been deployed to physical embedded targets.

---

## 5. Installation & Verification

### Prerequisites
- Python 3.10 or 3.11
- `make`
- [`uv`](https://docs.astral.sh/uv/) (recommended for fast, offline wheel builds)

### 1. Build & Setup
Bootstrap the local offline portfolio virtual environment:
```sh
make setup
# Alternatively: sh scripts/bootstrap_portfolio.sh
```
This builds hermetic wheels for `adi-common`, `adi-runtime`, and `adi-app` into `.release/wheels/` and installs them into `.release/venv/`.

### 2. Run Test Suites
Execute the complete test suite across all three packages:
```sh
make test
```
*Current verified baseline:* **438 tests passing** (157 in `adi-common`, 61 in `adi-runtime`, 220 in `adi-app`).

### 3. Verify Historical Hashes
Verify the cryptographic integrity of baseline project records:
```sh
make verify-hashes
```
*Expected output:* `historical manifest: 2 verified, 0 failure(s)`.

### 4. Verify Relocated Wheel Installation
Run the automated release packaging test that builds wheels, installs them into a temporary isolated environment, and validates CLI entry points and schema contracts:
```sh
make verify-portfolio
```
*Expected output:* JSON verification report with `"packaging_verification": "PASS"` and all CLI and contract checks passing.

### 5. Verify Stage 11 Schema & Semantic Invariants
Execute the comprehensive 32-check specification verification suite:
```sh
make verify-schema
# Or directly: .release/venv/bin/python scratch/verify_stage11_schema.py
```
*Expected output:* `ALL 32 EXECUTABLE CHECKS PASSED (32/32) — ZERO REGRESSIONS`.

---

## 6. Synthetic Demos & CLI Commands

All CLI commands run offline using the installed entry point `.release/venv/bin/adi-portfolio`.

### 1. CLI Scaffold Demo
Verify that the installed CLI executable responds:
```sh
.release/venv/bin/adi-portfolio demo
```

### 2. Multi-Scenario Evaluation
Run the scenario evaluation engine over synthetic test vectors:
```sh
.release/venv/bin/adi-portfolio evaluate --suite adi-app/tests/fixtures/scenarios/suite_all_pass.json
```
Evaluates normal, anomalous, and saturated signal windows against expected outcomes.

### 3. Time-Series Vibration Analysis
Process a CSV vibration recording through feature extraction and threshold evaluation:
```sh
.release/venv/bin/adi-portfolio analyze \
  --csv adi-app/tests/fixtures/scenarios/normal_5samples.csv \
  --metadata adi-app/tests/fixtures/scenarios/metadata_1000hz.json \
  --config adi-app/tests/fixtures/scenarios/config_1000hz_fs10_th2.json
```
Outputs computed window metrics (RMS, $V_{pp}$, Crest Factor, Variance) and the resulting anomaly decision (`NORMAL` or `ANOMALOUS`).

---

## 7. License & Redistribution

The source code and architecture in this repository are developed as an independent software prototype. All rights reserved. Do not redistribute without explicit permission. Third-party dependencies (`jsonschema`, `numpy`, `scipy`) remain governed by their respective open-source licenses.
