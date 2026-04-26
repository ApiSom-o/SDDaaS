# SDDaaS — Secure Data Deduplication-as-a-Service

**CSS451/454 Term Project** | SIIT, Thammasat University | 2025

A system for secure cloud file deduplication using **Convergent Encryption (CE)**, **CP-ABE** access control, and a **partitioned Bloom Filter tree** indexed by Client ID × File Type.

---

## Overview

SDDaaS eliminates duplicate file storage in the cloud while preserving both **privacy** and **fine-grained access control**. Instead of a single global index, SDDaaS routes each deduplication check through a two-level tree (Client → File Type) to a small, dedicated Bloom Filter — achieving O(1) search latency that stays constant as the dataset grows.

---

## Repository Structure

```
SDDaaS/
├── README.md
├── requirements.txt
└── experiments/
    ├── sddaas_experiment_final.py    # Experiment 1: SDDaaS vs Linear Search
    ├── sddaas_real_experiment.py     # Experiment 2: Real measurements (AES-256, BF FPR, latency)
    └── search_paper_somo_new.py      # Experiment 3: Analytical comparison vs 5 baseline systems
```

---

## Experiments

### 1. `sddaas_experiment_final.py` — SDDaaS vs Linear Search
Compares search time and storage cost between a naive linear scan and the SDDaaS system.

- **Search Time**: Linear O(n) vs SDDaaS O(1) across n = 100 → 50,000 files
- **Storage Cost**: Traditional (store all) vs SDDaaS (store unique only, 40% dedup ratio)
- Outputs: `sddaas_comparison.png`

### 2. `sddaas_real_experiment.py` — Real Measurements
All numbers come from actual Python execution — no simulation.

- **AES-256-GCM** encrypt/decrypt time vs file size (50KB → 1,600KB, avg of 5 runs)
- **Bloom Filter FPR**: Global BF (Li 2016 style) vs SDDaaS Partitioned BF (60 partitions), measured from real queries
- **SDDaaS Search Latency**: avg ± std across 500 queries per n
- Outputs: `sddaas_real_experiment.png`

### 3. `search_paper_somo_new.py` — Analytical Comparison vs Baselines
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

Tested on Python 3.10+.

---

## How to Run

### Run all experiments (recommended)

```bash
python main.py
```

Runs Experiment 1 and 2 sequentially. Output graphs (`.png`) are saved inside the `experiments/` folder.

### Run individually

```bash
# Experiment 1 — SDDaaS vs Linear Search
python experiments/sddaas_experiment_final.py

# Experiment 2 — Real AES & BF measurements
python experiments/sddaas_real_experiment.py

# Experiment 3 — 6-system analytical comparison
python experiments/search_paper_somo_new.py
```

---

## System Design

```
Upload Request (file, client_id, file_type)
        │
        ▼
  SHA-256(file) → hash h
        │
        ▼
  Tree[client_id][file_type] → BloomFilter
        │
   BF.search(h)?
   ├── YES → Duplicate detected → store reference pointer only
   └── NO  → New file → encrypt with CE + CP-ABE → store
```

**Key properties:**
- No false negatives (BF guarantee)
- Controlled false positive rate (< 0.025% with partitioned BFs at n=50,000)
- Per-client deduplication only — no cross-client information leakage by design

---

## Authors

Perabha Roiampaeng · Korapin Na Songkhla · Wanitcha Saengbundit · Apisara Korcharoenkiat

Supervised by Asst. Prof. Somchart Fugkeaw, SIIT, Thammasat University
