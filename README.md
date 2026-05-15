# SDDaaS — Secure Data Deduplication-as-a-Service

**CSS451/CSS454 Term Project** | Sirindhorn International Institute of Technology (SIIT), Thammasat University | 2025

> Perabha Roiampaeng · Korapin Na Songkhla · Apisara Kocharoenkiat · Wanitcha Saengbundit  
> Supervised by Asst. Prof. Somchart Fugkaew, School of ICT, SIIT

---

## Overview

SDDaaS proposes a **Secure Data Deduplication-as-a-Service** architecture for multi-tenant cloud storage combining:

- **Convergent Encryption (CE)** — derives a deterministic AES-256 key from `SHA-256(plaintext)`, enabling deduplication on encrypted data
- **CP-ABE** — wraps the convergent key under a user-defined access policy for fine-grained access control
- **Bloom Filters** — one dedicated filter per `(Client ID × File Type)` leaf node, achieving O(1) duplicate screening with near-zero false positives
- **Search Tree** — routes each query through `Root → Department → Client → File Type` before any Bloom filter probe, achieving constant-time search regardless of dataset size

---

## Repository Structure

```
SDDaaS/
├── README.md
├── requirements.txt
├── .gitignore
│
├── data/
│   ├── secure_dedup_50k.csv    # Main simulation dataset (500,000 records)
│   └── dataset.csv             # Employee–Department mapping (50,000 employees × 20 depts)
│
└── experiments/
    ├── SDDaaS_Comparison.py    # Experiment: SDDaaS vs 5 related-work systems (paper Section IV)
    └── SDDaaS_Demo.py          # Experiment: Interactive demo with local web UI
```

---

## Dataset Description

### `data/secure_dedup_50k.csv` — Main Simulation Dataset

| Column | Type | Description |
|---|---|---|
| `Emp_ID` | string | Employee (Client) ID — 50,000 unique clients |
| `Dept_ID` | string | Department ID — 20 departments |
| `File_Type` | string | File category (Database, Image, PDF, Document, SourceCode, Archive, Presentation, Spreadsheet, Log, Video) |
| `File_Size_KB` | int | File size in KB (range: 50–10,000 KB, avg: 5,022 KB) |
| `File_Hash` | string | SHA-256 fingerprint of the file |
| `Upload_Date` | date | Upload timestamp |
| `Is_Duplicate` | bool | Ground-truth duplicate label |

**Key statistics (match Table I in the paper):**

| Parameter | Value |
|---|---|
| Total records | 500,000 |
| Unique clients | 50,000 |
| Departments | 20 |
| File types | 10 |
| Duplicate ratio | 15.05% (75,246 duplicates) |
| Bloom Filter partitions | 500,000 (one per client–file-type leaf) |

### `data/dataset.csv` — Employee–Department Mapping

| Column | Description |
|---|---|
| `Emp_ID` | Employee ID (E00001 – E50000) |
| `Dept_ID` | Department ID (D01 – D20) |

Used by `SDDaaS_Demo.py` to seed the interactive demo with realistic department structure.

---

## Installation

Requires **Python 3.10 or later**.

```bash
git clone https://github.com/ApiSom-o/SDDaaS.git
cd SDDaaS
pip install -r requirements.txt
```

Dependencies: `matplotlib`, `pandas`, `tabulate`  
All other imports (`math`, `hashlib`, `http.server`, etc.) are Python standard library — no additional installation needed.

---

## How to Run

### Experiment 1 — SDDaaS vs Related Works

**File:** `experiments/SDDaaS_Comparison.py`

This script reproduces the four evaluation graphs from **Section IV** of the paper:

| Graph | Metric | Systems compared |
|---|---|---|
| Graph 1 | Search Latency (µs) | All 6 systems (lower is better) |
| Graph 2 | Storage Used (MB) | 4 systems (lower is better) |
| Graph 3 | False Positive Rate (%) | 4 systems (lower is better) |
| Graph 4 | Storage Saved (%) vs duplicate ratio | 4 systems (higher is better) |

```bash
cd experiments
python3 SDDaaS_Comparison.py
```

**Expected output:**
- Terminal: dataset summary + 4 result tables matching Tables II–V in the paper
- File saved: `experiments/sddaas_v8.png` — all 4 graphs combined into one figure

**Scale points tested:** 10K, 50K, 100K, 200K, 350K, 500K files  
**Runtime:** approx. 10–30 seconds

---

### Experiment 2 — Interactive Demo

**File:** `experiments/SDDaaS_Demo.py`

Launches a local web server that simulates the full SDDaaS pipeline step-by-step.

```bash
cd experiments
python3 SDDaaS_Demo.py
```

**Expected output:**
- Terminal prints the local server address (e.g. `http://localhost:8080`)
- Browser opens automatically
- In the UI: select Department and Employee → upload any file → observe the full pipeline (Tree routing → Bloom Filter bit-array probe → Metadata Index fallback → result)

Press **`Ctrl+C`** in the terminal to stop the server.

> The demo pre-seeds **5,003 entries per client** (3 real file hashes + 5,000 random hashes) to simulate realistic 500,000-partition scale.

---

## Related Work Simulation Methodology

> As stated in the paper (Section IV): *"All baseline systems were simulated from their published algorithmic designs and mathematical complexity models, as original source code was unavailable."*

`SDDaaS_Comparison.py` implements each baseline as a Python class derived from a common abstract base (`ComparisonSystem`). Each class models latency, storage, FPR, and deduplication efficiency using mathematics from the respective papers:

| Class | System | Source | Key model |
|---|---|---|---|
| `Li2016` | Differential Bloom Filter | Z. Li et al., IEEE ICSESS 2016 | Two BFs (SC + CDC); capacity factor 0.70; FPR weighted by chunk ratio (87%/13%) |
| `Douceur2002` | CE + SALAD | J. R. Douceur et al., IEEE ICDCS 2002 | Exact content-hash via DHT; FPR = 0%; latency scales O(n) |
| `Xiong2019` | SRRS (RAT + DCF) | H. Xiong et al., IEEE Access 2019 | B⁺-tree RAT (order 3) + Dynamic Count Filter; DCF FPR = BF × 1.10; latency O(log n) |
| `TSCF2021` | Two-Stage Cuckoo Filter | J. Liu et al., IEEE MSN 2021 | 12-bit fingerprint, 4 slots/bucket; FPR = 2b/2^f; latency O(log n) |
| `FCDedup2023` | Fog+Cloud Two-Level Dedup | J. Song et al., IEEE TPDS 2023 | Bilinear pairing; latency O(n) at rate n/1,024 per query |
| `SDDaaS` | **This work** | — | Partitioned BF (N/500,000 items/leaf); O(1) latency; 3-layer FPR mitigation |

All parameters (FPR target, CP-ABE overhead, CE key size, duplicate reference size) are defined as named constants at the top of `SDDaaS_Comparison.py` for full reproducibility.

---

## System Design Summary

Five-phase pipeline (paper Section III-B):

```
Phase 1: File Upload     Client → TLS/SSL → SDDaaS Gateway
Phase 2: Encryption      K = SHA-256(M) | CT = AES-256(K,M) | CTK = CP-ABE(PK,K,T)
Phase 3: Indexing        Metadata Index built; {CT,CTK} sent to Cloud Storage
Phase 4: Deduplication   Tree routing (Dept→Client→FileType) → Bloom Filter probe
                          → if "probably duplicate": deterministic Metadata Index fallback
Phase 5: Update/Return   New file: store CT+CTK, insert into BF+tree
                          Duplicate: record 64-byte reference only
```

**Key guarantees:**
- **No false negatives** — Layer 3 deterministic fallback ensures no true duplicate is missed
- **Near-zero FPR** — partitioned BF keeps per-leaf load ≈ 1.5 items at N = 500K (FPR < 0.0001%)
- **O(1) search** — hierarchical routing bounds every query to one leaf before BF probe

---

## Authors

| Name | Student ID | Email |
|---|---|---|
| Perabha Roiampaeng | 6622770533 | 6622770533@g.siit.tu.ac.th |
| Korapin Na Songkhla | 6622771499 | 6622771499@g.siit.tu.ac.th |
| Apisara Kocharoenkiat | 6622772695 | 6622772695@g.siit.tu.ac.th |
| Wanitcha Saengbundit | 6622780631 | 6622780631@g.siit.tu.ac.th |

**Supervisor:** Asst. Prof. Somchart Fugkaew — somchart@siit.tu.ac.th
