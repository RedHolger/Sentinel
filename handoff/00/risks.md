# Risk Analysis — Stage 00 Baseline

Initial architectural and deployment risk register.

| Risk ID | Severity | Category | Risk Description | Mitigation Strategy |
|---|---|---|---|---|
| RSK-01 | High | Hardware Gate | False hardware verification: asserting embedded target execution from host CPU simulation | Require explicit device serial connection, firmware logs, and hardware telemetry |
| RSK-02 | High | Memory Overrun | SRAM bank overflow on ultra-low-power microcontrollers (e.g. 512KB limit) | Enforce static tensor allocation, weight quantization (INT8/INT4), and shape specialization |
| RSK-03 | Medium | Parity Drift | Quantized integer weights drifting outside acceptable numerical tolerance | Multi-sample numerical parity checks and greedy output agreement tests |
| RSK-04 | Medium | Hallucination | Assistant making unverified claims on incomplete sensor facts | Fail closed with deterministic ABSTAIN whenever required evidence is absent |
