# SDDaaS — Secure Data Deduplication-as-a-Service
**CSS451|454 Term Project** | SIIT, Thammasat University | 2025

A system for secure cloud file deduplication using **Convergent Encryption (CE)**, **CP-ABE** access control, and a **partitioned Bloom Filter tree** indexed by Client ID × File Type.

---

## Overview

SDDaaS eliminates duplicate file storage in the cloud while preserving both **privacy** and **fine-grained access control**. Instead of a single global index, SDDaaS routes each deduplication check through a two-level tree (Client → File Type) to a small, dedicated Bloom Filter — achieving near-constant search latency regardless of dataset size.

---

## Repository Structure
---

## Experiments

### 1. `SDDaaS_Baseline.py` — SDDaaS vs Traditional
Compares search time and storage cost between a naive linear scan and the SDDaaS system.
- **Search Time**: Linear O(n) vs SDDaaS O(1) across n = 100 → 50,000 files
- **Storage Cost**: Traditional (store all) vs SDDaaS (store unique only, 40% dedup ratio)
- Outputs: `sddaas_comparison.png`

### 2. `SDDaaS_Demo.py` — Interactive Demo
Interactive demonstration of the SDDaaS system.
- Simulates file upload, deduplication check, and access control flow
- Runs a local web server for visualization
- **Note:** Once the server starts, open your browser and go to http://localhost:5500 to view the demo. Press Ctrl+C to stop the demo server and continue to the next experiment.

### 3. `SDDaaS_Relatedwork.py` — Analytical Comparison vs Baselines
Compares SDDaaS against 5 published systems using analytical models calibrated to each paper's reported parameters.

| System | Source |
|---|---|
| Li 2016 | Differential Bloom Filter (ICSESS 2016) |
| Douceur 2002 | Convergent Encryption + SALAD (ICDCS 2002) |
| Xiong 2019 | SRRS: CE + Role Authorized Tree (IEEE Access 2019) |
| TSCF 2021 | Two-Stage Cuckoo Filter (IEEE MSN 2021) |
| FCDedup 2023 | Two-Level Dedup for Fog Computing (IEEE TPDS 2023) |
| **SDDaaS** | **This work** |

Metrics: Search Latency (µs), Storage Used (MB), False Positive Rate (%), Deduplication Efficiency (%)
- Outputs: `sddaas_comparison_full.png`

---

## Installation

```bash
pip install matplotlib numpy cryptography tabulate
```

Tested on Python 3.10+. Use `python3` instead of `python` if needed.

---

## How to Run

### Run all experiments (recommended)
```bash
python3 main.py
```
Runs all experiments sequentially. Output graphs (`.png`) are saved inside the `experiments/` folder.

### Run individually
```bash
# Experiment 1 — SDDaaS vs Linear Search
python3 experiments/SDDaaS_Baseline.py

# Experiment 2 — Interactive Demo
python3 experiments/SDDaaS_Demo.py

# Experiment 3 — Analytical comparison vs baseline systems
python3 experiments/SDDaaS_Relatedwork.py
```

---

## System Design
**Key properties:**
- No false negatives (BF guarantee)
- Controlled false positive rate (< 0.025% with partitioned BFs at n=50,000)
- Per-client deduplication only — no cross-client information leakage by design

---

## Authors

Perabha Roiampaeng · Korapin Na Songkhla · Wanitcha Saengbundit · Apisara Korcharoenkiat  
Supervised by Asst. Prof. Somchart Fugkeaw, SIIT, Thammasat University
