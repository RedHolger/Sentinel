# Technical Architecture Walkthrough (Target Architecture & Baseline Status)

> [!NOTE]
> **Implementation Status Notice (Baseline Stage):**  
> The diagrams and component flows described below represent the **target / proposed system architecture**. At the current baseline, the repository contains package skeletons (`adi-common` v0.1.0, `adi-runtime` v0.1.0) and a CLI entry-point scaffold (`adi-app` v0.1.0). Pinned reference models, analytical memory/SRAM calculations, contract schemas, and deterministic verification engines are **unimplemented** and scheduled for subsequent development stages.

## 1. System Overview (Target Design)

The target ADI Edge Portfolio design aims to provide an evidence-governed edge AI runtime and deterministic application layer for resource-constrained Analog Devices platforms (microcontrollers, DSPs, and sensor bridges).

```text
┌─────────────────────────────────────────────────────────────┐
│             ADI App / Assistant [PROPOSED]                  │
│   (Query Parser → Requirement Graph → Deterministic Verifier)│
│       * Current status: Baseline CLI scaffold only *         │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  ADI Runtime [PROPOSED]                     │
│   (Quantized Execution → Parity Verification → Target Bridge)│
│       * Current status: Skeleton package (v0.1.0) *         │
└──────────────────────────────┬──────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────┐
│                  ADI Common [PROPOSED]                      │
│   (Canonical Versioned Schemas, Contracts, & Invariants)     │
│       * Current status: Skeleton package (v0.1.0) *         │
└─────────────────────────────────────────────────────────────┘
```

## 2. Component Boundaries & Current Implementation Status

1. **`adi-common` (Proposed Data Contracts & Schemas):**
   - *Target Scope:* Canonical single source of truth for JSON schemas and entity interfaces (`hardware_status`, `evidence_claim`, `decision`, `benchmark_result`). Strict invariants: hardware execution proof requires physical telemetry; decisions require verified citation links.
   - *Current Implementation Status:* **Unimplemented skeleton (`v0.1.0`)**. The `schemas/` directory is currently empty. Schema files and validator logic are planned for Stage 02.

2. **`adi-runtime` (Proposed Runtime & Hardware Staging):**
   - *Target Scope:* Pinned reference model execution, signal feature processing, analytical memory residency models (e.g., layer SRAM buffer calculations), and quantization tradeoff harnesses.
   - *Current Implementation Status:* **Unimplemented skeleton (`v0.1.0`)**. Reference models, analytical memory formulas, and target hardware bridges are planned for Stages 02–05.

3. **`adi-app` (CLI Scaffold Implemented; Verifier Proposed):**
   - *Target Scope:* Offline deterministic decision engine with strict citation resolution; fails closed on missing facts, ambiguous units, or unsupported predicates; packaged evaluation fixtures.
   - *Current Implementation Status:* **Baseline CLI scaffold implemented (`v0.1.0`)**. `adi-portfolio demo` returns baseline scaffold information (exit code 0); `adi-portfolio benchmark` and `adi-portfolio evaluate` fail closed with `status: NOT_IMPLEMENTED`, `hardware_status: UNVERIFIED`, and exit code 1. The deterministic verifier, requirement graph parser, and evaluation fixtures are planned for Stages 07–09.

## 3. Hardware Governance & Deployment

- Host CPU simulation must never emit `VERIFIED_HARDWARE` status.
- Hardware execution remains `UNVERIFIED` and execution `BLOCKED` until device serial logs, register dumps, or profiler traces are physically validated.
