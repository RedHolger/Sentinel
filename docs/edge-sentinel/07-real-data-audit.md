# 07-real-data-audit: Real Vibration Dataset Audit & Evaluation Protocol Design (Repaired)

**Document ID:** `docs/edge-sentinel/07-real-data-audit.md`  
**Stage:** 07 — Real Data Audit (Repair 03)  
**Author:** Edge Sentinel Research & Data Engineering  
**Prerequisite:** Stage 06 Accepted (`reviews/edge-sentinel/06-scenario-evaluation-repair-01.md`)  
**Supervisor Review Reference:** `reviews/edge-sentinel/07-real-data-audit-repair-02.md`  
**Status:** REPAIRED DRAFT — PENDING SUPERVISOR REVIEW  

---

## 1. Executive Summary & Local Workspace Inventory

### 1.1 Objective & Boundary Constraints
This document executes the Stage 07 repair specification (`docs/edge-sentinel/prompts/07-real-data-audit-repair-03.md`) following supervisor reviews (`reviews/edge-sentinel/07-real-data-audit.md`, `reviews/edge-sentinel/07-real-data-audit-repair-01.md`, and `reviews/edge-sentinel/07-real-data-audit-repair-02.md`). It establishes an evidence-governed audit of public vibration datasets and defines a conditional, descriptive evaluation protocol for the existing deterministic host RMS reference detector.

In strict compliance with project guidelines and supervisor review findings:
- **Zero Remote Downloads:** No remote datasets, archives, or candidate files were downloaded.
- **Zero Code Modification:** Application source code, test suites, contracts, schemas, and synthetic fixtures were preserved without modification.
- **Honest Gate Governance:** Missing physical parameters (transducer sensitivities, acquisition clipping limits, stored data units, and cross-record sampling rates) are kept strictly **UNKNOWN** and the corresponding analysis gates remain **BLOCKED**. No artificial shortcuts—such as conversion factors or nominal sensor range assumptions—are permitted.
- **Explicit Limitations of File Inspection:** File inspection alone may reveal array dimensions, variable names, or unscaled numeric sequences, but **cannot guarantee physical units or sampling rates without explicit primary documentation or calibration records**. Missing metadata remains strictly blocked; under no circumstances may sample rates be inferred from sample counts alone, or physical units/clipping limits inferred from signal amplitudes or observed maxima.
- **Strict Isolation of Synthetic Evaluator:** The accepted synthetic scenario evaluator (`adi-portfolio evaluate`) strictly requires `provenance == "synthetic"`. It remains untouched and isolated from real data.
- **No Unproven Specimen or Generalization Claims:** Physical bearing specimens across load runs are unproven in primary sources; conservative grouping is applied under local precautionary group IDs, and bearing/machine-independent generalization is marked **BLOCKED**.
- **Rigorous Statistical Reporting:** Overlapping windows and contiguous non-overlapping windows in continuous rotating machinery data are recognized as statistically dependent. Naive confidence intervals and independence assumptions (including $\lfloor N/W \rfloor$) are completely excluded; all pilot reporting is strictly descriptive with unambiguous denominators and explicit null handling for zero-count cases.

### 1.2 Local Workspace Inventory
A recursive audit of the workspace (`/Users/manuvashistha/Developer/ADI`) was executed across all extensions associated with structured time-series data (`.csv`, `.mat`, `.parquet`, `.h5`, `.hdf5`, `.dat`, `.raw`, `.bin`):

```text
LOCAL DATA AUDIT FINDINGS:
- Local real machine recordings: 0
- Local vibration sensor logs: 0
```

The only tabular files identified are:
1. **Synthetic Scenario Fixtures (`adi-app/tests/fixtures/scenarios/`):**
   - Six hand-authored synthetic CSV files (`normal_5samples.csv`, `saturation_5samples.csv`, `mixed_15samples.csv`, `short_3samples.csv`, `equality_5samples.csv`, `anomaly_5samples.csv`).
   - All explicitly carry `provenance: "synthetic"`, small hand-crafted numbers (e.g. `1.0`, `2.5`), and row lengths under 16 samples. They serve strictly as regression unit tests and do not represent physical machine data.
2. **Environment Validation Tables (`.release/venv/...`):**
   - NumPy test tables (`umath-validation-set-*.csv`, `pcg64-testset-*.csv`) containing mathematical verification vectors.

**Inventory Conclusion:** Exactly zero real vibration recordings exist in the repository.

---

## 2. Systematic Survey of Candidate Datasets with Directly Inspected Evidence

Two prominent public vibration datasets widely cited in condition monitoring literature were audited against directly inspected primary documentation:
1. **Case Western Reserve University (CWRU) Bearing Data Center**
2. **Paderborn University Bearing DataCenter (KAt)**

Every material claim regarding setup, rates, units, and licensing is anchored below to primary documentation inspected during audit reviews.

---

### 2.1 Candidate A: Case Western Reserve University (CWRU) Bearing Data Center

#### A. Directly Inspected Primary Sources (Full-Page Inspection)
1. **Apparatus & Procedures Page:**
   - **URL:** `https://engineering.case.edu/bearingdatacenter/apparatus-and-procedures`
   - **Access Method / Date:** Direct full-page HTML inspection, November 16, 2026.
   - **Faithful Paraphrase:** Test stand consists of a 2 hp Reliance Electric induction motor, a torque transducer/encoder, a dynamometer, and control electronics. Single-point defects were introduced using electro-discharge machining (EDM) on SKF deep-groove ball bearings (Drive End: 6205-2RS JEM; Fan End: 6203-2RS JEM) with defect diameters of 7, 14, 21, and 28 mils. Accelerometers were attached with magnetic bases at the 12 o'clock position at the Drive End (DE) and Fan End (FE) of the motor housing, and on the motor base plate (BA). Digital data was collected at 12,000 samples/sec, and 48,000 samples/sec for drive-end bearing experiments.
   - **Omissions in Source:** Transducer model, transducer sensitivity (mV/g), sensor gain settings, acquisition clipping voltage, and output physical units are **omitted**.
2. **Download Overview Page:**
   - **URL:** `https://engineering.case.edu/bearingdatacenter/download-data-file`
   - **Access Method / Date:** Direct full-page HTML inspection, November 16, 2026.
   - **Exact Quotation:**
     > *"Data was collected for normal bearings, single-point drive end and fan end defects.  Data was collected at 12,000 samples/second and at 48,000 samples/second for drive end bearing experiments.  All fan end bearing data was collected at 12,000 samples/second."*
     > *"Data files are in Matlab format.  Each file contains fan and drive end vibration data as well as motor rotational speed.  For all files, the following item in the variable name indicates: DE - drive end accelerometer data, FE - fan end accelerometer data, BA - base accelerometer data, time - time series data, RPM - rpm during testing"*
   - **Faithful Paraphrase of Rate Scope:** The page describes sampling rates for experimental categories (12 kHz and 48 kHz for drive-end bearing experiments; 12 kHz for fan-end bearing experiments). It does **not** specify the sampling rate for the normal baseline files.
3. **Normal Baseline Data Index:**
   - **URL:** `https://engineering.case.edu/bearingdatacenter/normal-baseline-data`
   - **Access Method / Date:** Direct full-page HTML inspection, November 16, 2026.
   - **Faithful Paraphrase:** Table lists motor load (0, 1, 2, 3 HP) and approximate motor speed (1797, 1772, 1750, 1730 RPM) corresponding to data files `Normal_0` (`97.mat`), `Normal_1` (`98.mat`), `Normal_2` (`99.mat`), `Normal_3` (`100.mat`). Current download links point to `https://engineering.case.edu/sites/default/files/97.mat`, etc.
   - **Omissions in Source:** The table provides load and speed only; it does **not** list sampling rates per channel.
4. **12k Drive End Bearing Fault Data Index:**
   - **URL:** `https://engineering.case.edu/bearingdatacenter/12k-drive-end-bearing-fault-data`
   - **Access Method / Date:** Direct full-page HTML inspection, November 16, 2026.
   - **Faithful Paraphrase:** Index lists single-point fault files by defect diameter (0.007", 0.014", etc.) and load. For 0.007" inner race, file `IR007_0` links to `https://engineering.case.edu/sites/default/files/105.mat` (0 HP / 1797 RPM) and `IR007_1` links to `https://engineering.case.edu/sites/default/files/106.mat` (1 HP / 1772 RPM).
5. **Bibliographic Note on Secondary Literature:**
   - *Reference:* Smith, W. A., & Randall, R. B. (2015). *"Rolling element bearing diagnostics using the Case Western Reserve University data: A benchmark study."* Mechanical Systems and Signal Processing, 64–65, pp. 100–131. DOI: `10.1016/j.ymssp.2015.04.021`.
   - *Evidentiary Limitation:* The publisher DOI returned HTTP 403 (paywall/forbidden) during review. In accordance with project instructions, bibliographic citations and secondary snippets are treated strictly as leads, not verified full-text inspection. We make zero assertions about stored array units or sensitivities based on inaccessible secondary papers.

#### B. Evidentiary Ledger of Parameters (Documented vs. Inferred vs. Unknown)

| Parameter / Dimension | Evidentiary Status | Supporting Primary Evidence | Architectural Consequence |
|---|---|---|---|
| **Motor & Test Rig** | **Documented** | CWRU Apparatus page: 2 hp Reliance Electric motor, dynamometer, SKF 6205-2RS JEM drive-end bearing. | Single test stand; machine independence unavailable. |
| **Fault Labels** | **Documented** | CWRU Apparatus page: Seeded EDM single-point flaws (0.007", 0.014", etc.) on inner/outer races. | Recording-level flaw label documented; window-level flaw signature presence not guaranteed. |
| **Sensor Locations** | **Documented** | CWRU Apparatus page: Single-axis accelerometers magnetically mounted at 12 o'clock position on DE and FE housings. | DE and FE identify separate physical sites on the motor; they are **NOT** orthogonal spatial axes ($x, y$) at one location. |
| **Normal Baseline Rates** | **Unknown (Unstated)** | CWRU normal-baseline table lists load/speed only; download page states rates for drive-end and fan-end fault experiments but omits baseline. | **Sampling rate per channel in `97.mat`/`98.mat` is UNKNOWN.** |
| **12k Fault Experiment Rate** | **Documented (Category-level)** | CWRU 12k index page title: "12k Drive End Bearing Fault Data". | Experiment category is 12 kHz, but individual channel rates (`FE`, `BA`) inside files are unconfirmed on the index. |
| **Physical Specimen Identity** | **Unknown (Unproven)** | Primary documentation does not state whether the exact same physical bearing was tested across load variations or swapped. | **Physical bearing specimen identity across runs is UNKNOWN.** Conservative grouping required. |
| **Transducer Sensitivity** | **Unknown (Unstated)** | Primary site omits transducer model, sensitivity (mV/g), and gain settings. | **Physical acceleration unit is UNKNOWN. Analysis gate BLOCKED.** |
| **Acquisition Full Scale** | **Unknown (Unstated)** | Primary site omits DAT recorder / DAQ input voltage range and clipping limits. | **Sensor full scale is UNKNOWN. Analysis gate BLOCKED.** |
| **Candidate File URLs** | **Documented (Exposed on Index)** | Official links on HTML index: `https://engineering.case.edu/sites/default/files/{97,98,105,106}.mat`. | Current URLs verified; files remain unverified pending download. |
| **File Byte Sizes & Hashes** | **Unknown (Unverified)** | Files have not been downloaded or inspected locally. | Manifest marks digests `UNVERIFIED_PENDING_DOWNLOAD`. |
| **Licensing & Terms** | **Unknown (Unstated)** | Primary site carries *© 2024 Case Western Reserve University* with no license text or grant. | `local_research_permitted: "UNKNOWN_UNSTATED"`. Public web availability $\ne$ affirmative grant. |

---

### 2.2 Candidate B: Paderborn University Bearing DataCenter (KAt)

#### A. Primary Sources & Inspected Passage
1. **Institutional Dataset Portal:**
   - **URL:** `https://mb.uni-paderborn.de/kat/forschung/bearing-datacenter`  
     *(Note: The older path `/datacenter/bearing-datacenter` cited in older literature returned HTTP 404 during review).*
   - **Access Method / Date:** Direct full-page HTML inspection, November 16, 2026.
   - **Faithful Paraphrase:** Chair of Design and Drive Technology (KAt), Paderborn University. Dataset consists of 32 bearings (6 undamaged, 12 artificial damage, 14 accelerated fatigue damage) measured across 4 operating conditions. Page explicitly states licensing terms: released under the **Creative Commons Attribution-NonCommercial 4.0 International License (CC BY-NC 4.0)**.
2. **Primary Technical Publication:**
   - **Reference:** Lessmeier, C., Kimotho, J. K., Zimmer, D., & Sextro, W. (2016). *"Condition Monitoring of Bearing Damage in Electromechanical Drive Systems by Using Motor Current Signals of Electric Motors: A Benchmark Data Set for Data-Driven Classification."* Proceedings of the European Conference of the PHM Society, 3(1).
   - **Inspected Passage Reference & Prior Access:** Section 4.1 (*Test rig and measurement system*), printed page 7. URL: `https://mb.uni-paderborn.de/fileadmin-mb/kat/PDF/Veroeffentlichungen/20160703_PHME16_CM_bearing.pdf`. Prior supervisor review retrieved and inspected Section 4.1 directly; fresh HTTP fetching timed out during supervisor review, and no new PDF retrieval was performed in this turn.
   - **Faithful Paraphrase of Supported Facts:** Section 4.1 documents that housing acceleration is measured using a piezoelectric accelerometer (Model No. 336C04, PCB Piezotronics, Inc.) with a charge amplifier (Type 5015A, Kistler Group). Signals are filtered by an analog 30 kHz low-pass filter (Kistler Group) and sampled at a 64 kHz sampling rate alongside motor current.
   - **Omissions & Unsupported Items:** Filter order is unevidenced in the inspected primary text (marked unknown). Charge amplifier range settings, acquisition clipping limits, and stored data array units are omitted in primary documentation (marked unknown; analysis gates blocked).

#### B. Evidentiary Ledger of Parameters (Documented vs. Inferred vs. Unknown)

| Parameter / Dimension | Evidentiary Status | Supporting Primary Evidence | Architectural Consequence |
|---|---|---|---|
| **Accelerometer Model** | **Documented** | Lessmeier et al. (2016) Section 4.1: Model PCB 336C04. | Sensor model identified. |
| **Charge Amplifier** | **Documented** | Lessmeier et al. (2016) Section 4.1: Kistler Type 5015A. | Amplifier model identified. |
| **Analog Filter** | **Documented** | Lessmeier et al. (2016) Section 4.1: 30 kHz low-pass filter (filter order unstated/unknown). | Cutoff frequency documented; filter order unknown. |
| **Sampling Frequency** | **Documented** | Lessmeier et al. (2016) Section 4.1: 64 kHz sampling rate. | High sampling rate ($64,000\text{ Hz}$). |
| **Dataset License** | **Documented** | Current institutional portal: CC BY-NC 4.0. | Non-commercial research permitted with attribution; distinct from paper license. |
| **Amplifier Gain / Range Setting** | **Unknown** | Section 4.1 omits the selectable measuring range configured on the Kistler 5015A. | Actual electrical scale factor is unproven. |
| **Stored Signal Units** | **Unknown / Unverified** | Section 4.1 describes hardware but does not state whether array data stores calibrated acceleration or raw A/D units. | **Stored unit unknown; cannot be verified without explicit documentation. Gate BLOCKED.** |
| **Acquisition Full Scale** | **Unknown** | Section 4.1 omits A/D converter input range and clipping limits. | Nominal PCB 336C04 range $\ne$ acquisition clipping limit. **Gate BLOCKED.** |
| **Packaging & Modality** | **Documented** | KAt portal: Distributed as multi-modal datasets (vibration, current, torque, speed) in compressed archive format. | Multi-modal structure requires demuxing and format conversion. |

**Evaluation of Fallback:** Paderborn University is **NOT** plug-and-play compatible out of the box. While hardware models and sampling frequency are documented, the stored array units, acquisition range settings, and DAQ clipping limits remain unverified in primary documentation. Its analysis gates for units and full scale are also **BLOCKED**.

---

### 2.3 Note on Scope of Evaluated Candidates
Per the Stage 07 prompt instructions, evaluating two credible candidate datasets satisfies the comparison requirement. CWRU and Paderborn represent the primary condition-monitoring benchmarks in the literature and provide rigorous primary documentation. Datasets with unheadered continuous-degradation archives (such as NASA IMS) were excluded from detailed matrix comparison to avoid unevidenced sample-count/rate conflations or speculative archive sizing.

---

## 3. Comparative Matrix Across Candidate Datasets

| Dimension | Case Western Reserve Univ. (CWRU) | Paderborn Univ. (KAt) |
|---|---|---|
| **Primary Institutional Host** | Case School of Engineering (`case.edu`) | Paderborn Univ. KAt (`uni-paderborn.de`) |
| **Primary Reference Passage** | Apparatus & download subpages | Lessmeier et al. (2016) Section 4.1 (printed p. 7) |
| **Fault Ground Truth** | **Discrete seeded EDM flaws** | **Discrete healthy vs damaged** |
| **Mechanical Coupling** | Single bearing per test on DE/FE | Single bearing on modular test rig |
| **Primary Sensor Placement** | DE housing (12 o'clock), FE housing | Adapter top on bearing housing |
| **Spatial Semantics** | **Distinct housing locations** (DE $\ne$ FE) | Single accelerometer location |
| **Documented Sampling Rate** | **Unknown for baseline; 12 kHz for fault category** | **64 kHz synchronous** |
| **Stored Signal Units** | **UNKNOWN_UNSTATED** in primary pages | **UNKNOWN / UNVERIFIED** in MAT file |
| **Acquisition Full Scale** | **UNKNOWN_UNSTATED** in primary pages | **UNKNOWN** (clipping limits unrecorded) |
| **Native Storage Format** | MATLAB binary `.mat` (v4/v5) | MATLAB struct in compressed archive |
| **License / Terms** | **UNKNOWN_UNSTATED** (*© 2024 CWRU*) | **CC BY-NC 4.0** on institutional site |
| **Pipeline Suitability** | **Recommended Pilot Candidate** (with blocked gates) | Heavy fallback (format/rate friction) |

---

## 4. Conditional Pilot Dataset Recommendation & Candidate File Specifications

### 4.1 Conditional Recommendation Justification
We conditionally recommend a **single-channel Drive End candidate subset of the Case Western Reserve University (CWRU) dataset** as an evaluation pilot, while recognizing that **core measurement gates remain blocked**.

**Justification:**
1. **Discrete Fault Demarcation:** Clear, unarguable separation between undamaged baseline recordings and seeded single-point bearing flaws.
2. **Bounded Pilot Subset:** Restricting candidate scope to 4 candidate CWRU files (DE channel) provides a compact, targeted evaluation candidate without downloading full multi-gigabyte or unverified repositories.
3. **Transparent Governance:** CWRU's gaps (unstated units, unstated clipping limits, unconfirmed native sampling rates) are clearly isolated as formal contract gates.

### 4.2 Candidate Pilot File Specifications (4 Files, Zero Downloaded)

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      CANDIDATE PILOT SUBSET (CWRU DRIVE END)                     │
│                                                                                 │
│   DESCRIPTIVE DEVELOPMENT / CALIBRATION (Motor Load: 1 HP, Speed: ~1772 RPM)    │
│   ├── 98.mat  : Normal Baseline (SKF 6205-2RS, DE/FE channels, rate unstated)  │
│   └── 106.mat : 0.007" Inner Race Flaw (SKF 6205-2RS, DE/FE/BA channels)       │
│                                                                                 │
│   DESCRIPTIVE HELD-OUT TEST (Motor Load: 0 HP, Speed: ~1797 RPM)                │
│   ├── 97.mat  : Normal Baseline (SKF 6205-2RS, DE/FE channels, rate unstated)  │
│   └── 105.mat : 0.007" Inner Race Flaw (SKF 6205-2RS, DE/FE/BA channels)       │
└─────────────────────────────────────────────────────────────────────────────────┘
```

#### Detailed File Profiles
1. **`98.mat` (Calibration Normal Baseline Candidate):**
   - **Current Index URL:** `https://engineering.case.edu/sites/default/files/98.mat`
   - **Condition:** Normal baseline bearing (undamaged).
   - **Precautionary Specimen Group:** `precautionary_group_cwru_normal`.
   - **Physical Specimen Identity:** `UNKNOWN_UNPROVEN_IN_PRIMARY_SOURCES`.
   - **Operating Load / Speed:** 1 HP load, $\approx 1772\text{ RPM}$ ($29.53\text{ Hz}$).
   - **Documented Channels:** Drive End (`DE`), Fan End (`FE`).
   - **Sampling Rate Status:** `UNKNOWN_UNSTATED_IN_PRIMARY_SOURCES`.
   - **Physical Units / Full Scale:** `UNKNOWN_UNSTATED`.
   - **SHA-256 Digest:** `UNVERIFIED_PENDING_DOWNLOAD`.
2. **`106.mat` (Calibration Bearing Fault Candidate):**
   - **Current Index URL:** `https://engineering.case.edu/sites/default/files/106.mat`
   - **Condition:** Inner race flaw, $0.007\text{ inch}$ ($7\text{ mils}$) defect diameter.
   - **Precautionary Specimen Group:** `precautionary_group_cwru_ir007`.
   - **Physical Specimen Identity:** `UNKNOWN_UNPROVEN_IN_PRIMARY_SOURCES`.
   - **Operating Load / Speed:** 1 HP load, $\approx 1772\text{ RPM}$ ($29.53\text{ Hz}$).
   - **Documented Channels:** Drive End (`DE`), Fan End (`FE`), Base Plate (`BA`).
   - **Sampling Rate Status:** 12 kHz category, but rates per individual channel unconfirmed on index.
   - **Physical Units / Full Scale:** `UNKNOWN_UNSTATED`.
   - **SHA-256 Digest:** `UNVERIFIED_PENDING_DOWNLOAD`.
3. **`97.mat` (Held-Out Test Normal Baseline Candidate):**
   - **Current Index URL:** `https://engineering.case.edu/sites/default/files/97.mat`
   - **Condition:** Normal baseline bearing (undamaged).
   - **Precautionary Specimen Group:** `precautionary_group_cwru_normal`.
   - **Physical Specimen Identity:** `UNKNOWN_UNPROVEN_IN_PRIMARY_SOURCES`.
   - **Operating Load / Speed:** 0 HP load, $\approx 1797\text{ RPM}$ ($29.95\text{ Hz}$).
   - **Documented Channels:** Drive End (`DE`), Fan End (`FE`).
   - **Sampling Rate Status:** `UNKNOWN_UNSTATED_IN_PRIMARY_SOURCES`.
   - **Physical Units / Full Scale:** `UNKNOWN_UNSTATED`.
   - **SHA-256 Digest:** `UNVERIFIED_PENDING_DOWNLOAD`.
4. **`105.mat` (Held-Out Test Bearing Fault Candidate):**
   - **Current Index URL:** `https://engineering.case.edu/sites/default/files/105.mat`
   - **Condition:** Inner race flaw, $0.007\text{ inch}$ ($7\text{ mils}$) defect diameter.
   - **Precautionary Specimen Group:** `precautionary_group_cwru_ir007`.
   - **Physical Specimen Identity:** `UNKNOWN_UNPROVEN_IN_PRIMARY_SOURCES`.
   - **Operating Load / Speed:** 0 HP load, $\approx 1797\text{ RPM}$ ($29.95\text{ Hz}$).
   - **Documented Channels:** Drive End (`DE`), Fan End (`FE`), Base Plate (`BA`).
   - **Sampling Rate Status:** 12 kHz category, but rates per individual channel unconfirmed on index.
   - **Physical Units / Full Scale:** `UNKNOWN_UNSTATED`.
   - **SHA-256 Digest:** `UNVERIFIED_PENDING_DOWNLOAD`.

### 4.3 Channel Mapping & Spatial Semantics
- **Location vs. Axis Semantics:** Drive End (`DE`) and Fan End (`FE`) identify separate physical sensor locations at opposite ends of the motor housing. They are **NOT** orthogonal spatial axes ($x, y$) of a single triaxial accelerometer.
- **Prohibited Action:** Combining DE as channel `"x"` and FE as channel `"y"` in a single recording violates spatial semantics.
- **Pilot Strategy:** Restrict candidate pilot evaluation strictly to a **single-channel Drive End (DE) recording** mapped to channel `"x"`. Document that multi-location sensing requires a contract extension supporting explicit sensor location identifiers (e.g. `sensor_location: "drive_end"`).

---

## 5. Grouped Split Strategy & Precautionary Independence Boundaries

### 5.1 Precautionary Grouping & Generalization Limitations
A critical finding of this audit is that **bearing-independent testing is unproven within this candidate pilot**:
- Primary CWRU documentation does not establish whether distinct physical bearing specimens were tested across load levels or whether identical bearings were swapped.
- Furthermore, all recordings originated from a single 2 hp Reliance Electric motor test stand.

> [!IMPORTANT]
> **Independence Invariant:** Partitioning candidate files across loads (e.g. 98/106 vs 97/105) does **NOT** measure machine independence or proven bearing independence. It measures only the detector's response to motor load variation on the **same test stand**.
>
> We establish **local precautionary groups**:
> - `precautionary_group_cwru_normal`: `{"97.mat", "98.mat"}`
> - `precautionary_group_cwru_ir007`: `{"105.mat", "106.mat"}`
>
> Generalization across independent bearings or independent machinery is explicitly **BLOCKED**.

### 5.2 Pre-Windowing Partition Timing
Splits are defined at the whole physical recording level **strictly before time-domain windowing**:
- Slicing a continuous recording into sub-windows and randomly distributing windows between calibration and test sets is **strictly prohibited**. Contiguous windows in rotating machinery share near-identical spectral envelopes, operational trends, and background noise, producing extreme data leakage.
- Each physical recording belongs 100% to either Development/Calibration or Held-Out Test.

### 5.3 Multi-Tier Duplicate and Overlap Detection Protocol
Because container hashes or metadata-inclusive digests fail to catch identical sample data enclosed in different containers or variable names, duplicate detection must operate across multiple independent layers:

1. **Layer 1: Exact Sample-Content Sequence Hashing:**
   - Compute a deterministic SHA-256 digest strictly across the IEEE 754 binary64 float bytes of the sample matrix alone:
     $$\text{hash}_{\text{samples}} = \text{SHA-256}(\text{bytes}(x[0 \dots N-1]))$$
   - This digest is completely invariant to file names, MAT container versions, variable naming, and dictionary metadata. Identical sample series are detected immediately.
2. **Layer 2: Temporal Sub-Sequence & Overlap Check:**
   - Compute prefix, suffix, and sliding block hashes across the sample sequence to detect whether one recording is an offset, truncated, or sliding sub-window of another recording.
3. **Layer 3: Correlation Heuristic & Conservative Grouping:**
   - Calculate normalized temporal cross-correlation:
     $$\rho_{xy}[k] = \frac{\sum_n (x[n] - \mu_x)(y[n+k] - \mu_y)}{\sqrt{\sum_n (x[n] - \mu_x)^2 \sum_n (y[n+k] - \mu_y)^2}}$$
   - **Labeling Notice:** Cross-correlation is explicitly classified as an **empirical heuristic**, not a mathematical proof of duplication.
   - **Grouping Action:** Any pair of recordings exhibiting peak correlation $\max_k |\rho_{xy}[k]| \ge 0.95$ without documented evidence of distinct physical runs must be classified as **unresolved suspected duplicates** and conservatively locked into the **same partition**. Never place suspected duplicates across calibration and test partitions.

---

## 6. Descriptive Evaluation Protocol & Statistical Inference Corrections

### 6.1 Elimination of Invalid Statistical Inferences
- **Statistical Invariant:** In continuous vibration time series of rotating machinery, contiguous non-overlapping windows share non-stationary operational dynamics, thermal drift, motor speed flutter, and spectral leakage across window boundaries.
- **Prohibited Inference:**
  > [!WARNING]
  > Replacing the overlapping window count with $\lfloor N/W \rfloor$ merely counts distinct blocks of samples; **it does NOT establish statistical independence ($i.i.d.$)**.
  > Computing naive binomial confidence intervals, standard errors, or p-values using $\lfloor N/W \rfloor$ as the degrees of freedom is **statistically invalid and strictly prohibited**.
- **Corrected Protocol:** For this small pilot, all reported metrics must be **purely descriptive** (raw integer counts, observed window outcomes, recording envelope outcomes, and exact denominators).

### 6.2 Development-Only Calibration & Frozen Quantile Protocol
1. Calibration uses strictly the Development/Calibration partition (`98.mat`).
2. **Quantile Target & Tie Convention:**
   - The calibration false-alarm rate (e.g. $\alpha = 0.01$, or 99th percentile) is an **empirical calibration target on the development set**, not a guaranteed bound on unseen test data.
   - Let $\{r_i\}_{i=1}^M$ be the window max-RMS values computed across non-overlapping windows of `98.mat`.
   - Specify the exact quantile method: linear interpolation on the sorted array ($Q_{0.99}(\{r_i\})$ with standard NumPy `method='linear'`). If ties occur at the threshold, the exact equality evaluates to `outcome = "ANOMALY"`, conforming strictly to contract invariant `score >= threshold`.
3. **Configuration Freezing:**
   - The computed threshold $\tau$ is frozen into a static, versioned configuration file before any inspection of the held-out test partition (`97.mat`/`105.mat`).
   - Zero parameter tuning, threshold shifting, or post-hoc threshold adjustment is permitted on test data.

### 6.3 Ground-Truth Label Honesty
- A recording-level label of `fault_type = "inner_race"` confirms only that a damaged bearing was mounted in the rig during the run.
- It does **not** prove that every short sub-window contains an observable impact or fault transient.
- Any windows in a fault recording whose RMS score falls below threshold must be recorded honestly as False Negatives ($\text{FN}$), rather than discarding them or retroactively relabeling them.

### 6.4 Unambiguous Reporting Denominators & Accounting Formulas
To prevent ambiguous or double-subtracted coverage metrics, accounting is strictly defined across recording and window levels:

#### A. Recording-Level Accounting
- $N_{\text{rec\_attempted}}$: Total recording inputs submitted for evaluation.
- $N_{\text{rec\_valid}}$: Recordings that pass operational envelope validation (valid sample rate, unit, channels, and length $\ge W$).
- $N_{\text{rec\_abstained}}$: Recordings failing envelope validation, producing a single recording-level abstention (`quality_status: FAIL`, `outcome: INSUFFICIENT_EVIDENCE`).
- **Exact Invariant:**
  $$N_{\text{rec\_attempted}} = N_{\text{rec\_valid}} + N_{\text{rec\_abstained}}$$
- For recording abstentions, **zero window boundaries are invented** ($N_{\text{eligible\_windows}} = 0$).

#### B. Window-Level Accounting (Recording-Valid Inputs Only)
- $N_{\text{eligible\_windows}} = \lfloor \frac{N_{\text{samples}} - W}{H} \rfloor + 1$: The exact count of complete non-ragged windows that fit in the recording.
- $N_{\text{scored\_windows}}$: Windows where all sample values are strictly below full scale ($\forall n, c: |x[n, c]| < \text{full\_scale}$), evaluated to `quality_status: PASS` with outcome `NORMAL` or `ANOMALY`.
- $N_{\text{win\_abstained}}$: Windows where potential sensor saturation is detected ($\exists n, c: |x[n, c]| \ge \text{full\_scale}$), evaluated to `quality_status: INDETERMINATE` with outcome `INSUFFICIENT_EVIDENCE` and reason `SENSOR_SATURATION_POSSIBLE`.
- **Exact Invariant:**
  $$N_{\text{eligible\_windows}} = N_{\text{scored\_windows}} + N_{\text{win\_abstained}}$$
- **Window Coverage:**
  $$\text{Coverage}_{\text{window}} = \begin{cases} \frac{N_{\text{scored\_windows}}}{N_{\text{eligible\_windows}}}, & \text{if } N_{\text{eligible\_windows}} > 0 \\ \text{null}, & \text{if } N_{\text{eligible\_windows}} = 0 \end{cases}$$
- **Descriptive Outcome Rates on Scored Windows:**
  $$\text{Rate}_{\text{ANOMALY}} = \begin{cases} \frac{N_{\text{ANOMALY}}}{N_{\text{scored\_windows}}}, & \text{if } N_{\text{scored\_windows}} > 0 \\ \text{null}, & \text{if } N_{\text{scored\_windows}} = 0 \end{cases}$$
  $$\text{Rate}_{\text{NORMAL}} = \begin{cases} \frac{N_{\text{NORMAL}}}{N_{\text{scored\_windows}}}, & \text{if } N_{\text{scored\_windows}} > 0 \\ \text{null}, & \text{if } N_{\text{scored\_windows}} = 0 \end{cases}$$

---

## 7. Compatibility Gap Ledger & Blocked Analysis Gates

Mapping candidate real vibration data to our current contract system reveals four blocked analysis gates:

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                                COMPATIBILITY GAP LEDGER                                │
│                                                                                        │
│  1. Ingestion Format  : MATLAB .mat (v4/v5) ──► Implementation gap (scipy uninstalled) │
│  2. Physical Units    : Stored units unknown──► BLOCKED GATE (Sensitivity omitted)     │
│  3. Sensor Full Scale : Saturation omitted  ──► BLOCKED GATE (Range omitted)           │
│  4. Cross-Record Rate : Rates unconfirmed   ──► BLOCKED GATE (Rates unverified)        │
│  5. Bearing Independence: Same rig used     ──► BLOCKED GATE (Holdout descriptive only)│
└────────────────────────────────────────────────────────────────────────────────────────┘
```

> [!WARNING]
> **Explicit Rule on File Inspection Limits:**
> Opening or parsing data files may reveal raw numeric matrices or dictionary variable names, but **cannot guarantee physical units or sampling rates without explicit primary metadata or calibration documentation**.
> Missing metadata remains strictly blocked; under no circumstances may sample rates be inferred from sample count alone, or physical units / clipping limits inferred from signal amplitude or observed sample maxima.

### Detailed Gap Profiles

| Gate ID | Target Contract Field | Evidentiary Reality | Status | Mandated Action |
|---|---|---|---|---|
| **GATE_PHYSICAL_ACCELERATION_UNIT** | `VibrationRecordingV1.acceleration_unit` | CWRU primary documentation omits accelerometer transducer sensitivity (mV/g), sensor gain settings, and physical output units. Stored values in distributed MAT arrays cannot be assumed to be physical acceleration. File inspection alone cannot guarantee units without explicit documentation; never infer units from signal amplitude or observed maxima. | **BLOCKED** | Keep gate blocked. No supervisor policy can turn uncalibrated numbers into measured acceleration. (A future exploratory contract variant could be reviewed separately, but is not implemented today). |
| **GATE_SENSOR_FULL_SCALE_RANGE** | `threshold_configuration.full_scale` | CWRU primary documentation omits hardware DAQ voltage input ranges and sensor clipping thresholds. Nominal sensor specifications from third-party literature do not establish acquisition chain limits. File inspection alone cannot establish clipping bounds without explicit documentation; never infer full scale from observed maxima. | **BLOCKED** | Keep gate blocked. Do not infer full scale from observed sample maxima or assume nominal sensor limits. |
| **GATE_CROSS_RECORD_SAMPLE_RATE_COMPATIBILITY** | `threshold_configuration.expected_sample_rate_hz` | Native sampling rates per channel are unconfirmed in primary documentation for normal baseline and individual fault channels. Selecting Drive End alone does not resolve cross-record rate compatibility between normal and fault recordings. File inspection alone cannot guarantee rates without explicit metadata; never infer rates from sample counts alone. | **BLOCKED** | Keep gate blocked. Cross-record comparisons are blocked until native rates are established directly from explicit documentation. Defer window duration, common-rate choice, and resampling design; no executable recipe is ready. |
| **GATE_BEARING_INDEPENDENCE** | `evaluation_protocol.independence` | Primary documentation does not establish distinct physical bearing specimens across load tests. All data originated from a single test stand. | **BLOCKED** | Keep independent generalization blocked. Protocol is restricted strictly to descriptive same-rig load-holdout behavior. |

#### Discussion of Future Resampling Requirements
If a future cross-rate reconciliation is considered:
- Resampling cannot simply be assumed or executed via naive decimation/interpolation.
- It must specify an explicit anti-alias low-pass filter with a defined common cutoff frequency, passband ripple, stopband attenuation, and linear phase response to prevent spectral aliasing and phase distortion.
- Full provenance metadata tracking the original sample rate, filter parameters, and resampled rate must be recorded.
- **Current Status:** No resampling is implemented or authorized.

---

## 8. Machine-Readable Readiness Manifest

The repaired machine-readable readiness manifest is persisted as an independent, validated standalone artifact:
- **File Path:** [`docs/edge-sentinel/07-real-data-readiness-manifest.json`](file:///Users/manuvashistha/Developer/ADI/docs/edge-sentinel/07-real-data-readiness-manifest.json)
- **Manifest ID:** `readiness_manifest_cwru_pilot_repair_03`
- **Schema Version:** `1.0.0`

### Key Manifest Assertions & Governance Summary
1. **Dataset Identification:** Identifies Case Western Reserve University (CWRU) Bearing Data Center with primary institutional host and direct inspection access date (`2026-11-16`).
2. **Licensing Status:** Explicitly records `category: "UNKNOWN_NO_EXPLICIT_GRANT"`, `license_grant: "UNSTATED"`, and `local_research_permitted: "UNKNOWN_UNSTATED"`. Factually notes that public web hosting does not constitute an affirmative redistribution or open-source grant.
3. **Candidate File Profiles:** Defines candidate subset (`97.mat`, `98.mat`, `105.mat`, `106.mat`) with `channel_sample_rates_status: "UNKNOWN_UNSTATED_IN_PRIMARY_SOURCES"`, `physical_unit: "UNKNOWN_UNSTATED"`, `sensor_full_scale: "UNKNOWN_UNSTATED"`, and URLs exposed on index marked `CANDIDATE_URL_EXPOSED_ON_INDEX_UNVERIFIED_DOWNLOAD`.
4. **Precautionary Grouping:** Assigns files to `precautionary_group_cwru_normal` and `precautionary_group_cwru_ir007` with `machine_independence_status: "UNAVAILABLE_SINGLE_TEST_RIG"` and `bearing_independence_status: "BLOCKED_UNPROVEN_SPECIMEN_IDENTITY"`.
5. **Blocked Gates:** Enumerates all four blocked analysis gates (`GATE_PHYSICAL_ACCELERATION_UNIT`, `GATE_SENSOR_FULL_SCALE_RANGE`, `GATE_CROSS_RECORD_SAMPLE_RATE_COMPATIBILITY`, `GATE_BEARING_INDEPENDENCE`), explicitly recording that file inspection alone cannot guarantee units or rates.
6. **Provenance Isolation:** Enforces strict preservation of `provenance: "synthetic"` for the existing scenario evaluator (`adi-portfolio evaluate`), requiring real dataset evaluation to remain isolated in a future evaluation command.

---

## 9. Stage Exit Condition & Next Steps

Stage 07 has completed the rigorous repair of the real-data audit, source evidence ledgers, candidate comparison, pre-windowing split design, descriptive evaluation protocol, and compatibility gap ledger without remote downloads, code changes, or fabricated metadata.

**Status of Stage 08:**  
Stage 08 is **NOT authorized** and remains **BLOCKED** until the supervisor explicitly resolves or scopes the blocked measurement gates (`GATE_PHYSICAL_ACCELERATION_UNIT`, `GATE_SENSOR_FULL_SCALE_RANGE`, and `GATE_CROSS_RECORD_SAMPLE_RATE_COMPATIBILITY`).

```text
STATUS: READY FOR SUPERVISOR REVIEW
```
