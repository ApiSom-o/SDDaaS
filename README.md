# SDDaaS — Secure Data Deduplication-as-a-Service Using Search Tree and Bloom Filter

> **CSS451-454 Term Project | SIIT, Thammasat University**

---

## 👥 Authors

| Name | Student ID |
|------|------------|
| Perabha Roiampaeng | 6622770533 |
| Korapin Na Songkhla | 6622771499 |
| Apisara Kocharoenkiat | 6622772695 |
| Wanitcha Saengbundit | 6622780631 |

**Advisor:** Asst. Prof. Dr. Somchart Fugkaew
**School of Information, Computer and Communication Technology (ICT)**
**Sirindhorn International Institute of Technology (SIIT), Thammasat University**

---

## 📌 Project Overview

**SDDaaS** (Secure Deduplication as a Service) is a cloud-storage deduplication system that uses a **3-level Search Tree** (Department → Client → File Type) combined with **Bloom Filters** to detect duplicate files with near-zero false positive rates while preserving data privacy through Convergent Encryption (CE) and CP-ABE overhead modeling.

### Key Features
- ✅ **O(1) 3-level tree routing** 
- ✅ **Partitioned Bloom Filters** 
- ✅ **3-layer false positive mitigation**
- ✅ **Near-zero FPR** 
- ✅ **Interactive web demo**

---

## 📁 Repository Structure

```
SDDaaS/
├── README.md
├── requirements.txt
├── demo.py                  # Web demo server (SDDaaS engine + interactive UI)
├── compare.py               # Simulation & comparison vs 5 related works
├── generate_test_dataset.py # Script to generate 5,000 unique test PDF files
├── dataset.csv              # Dataset 1 — 50,000 employees (Emp_ID, Dept_ID)
└── secure_dedup_50k.csv     # Dataset 2 — 500,000 file upload records
```

---

## 📊 Datasets

### `dataset.csv` — Employee Registry
| Column | Description |
|--------|-------------|
| `Emp_ID` | Employee ID (E00001 – E50000) |
| `Dept_ID` | Department ID (D01 – D20) |

- **50,000 employees** across **20 departments**
- Used by `demo.py` to seed the Bloom Filter tree at startup

### `secure_dedup_50k.csv` — File Upload Records
| Column | Description |
|--------|-------------|
| `Emp_ID` | Employee ID |
| `Dept_ID` | Department ID |
| `File_Type` | One of 10 types (Database, Image, PDF, Document, SourceCode, Archive, Presentation, Spreadsheet, Log, Video) |
| `File_Size_KB` | File size in KB |
| `File_Hash` | SHA-256 hash (truncated 16 hex chars) |
| `Upload_Date` | Upload timestamp |
| `Is_Duplicate` | Boolean duplicate flag |

- **500,000 records** | Duplicate ratio ≈ **15.05%** | Avg file size ≈ **5,022 KB**
- Used by `compare.py` for benchmark calculations

---

## ⚙️ Requirements

- Python **3.9+** — check with `python3 --version`
- pip — check with `pip3 --version`
- Git — check with `git --version`

---

## 🚀 How to Run (Step-by-Step)

### Step 1 — Clone the repository

```bash
git clone https://github.com/ApiSom-o/SDDaaS.git
```

### Step 2 — Enter the project folder

```bash
cd SDDaaS
```

### Step 3 — Verify datasets are present

The datasets are included in the repository and downloaded automatically when you clone.
Check that both files exist:

```bash
ls
```

You should see:
```
dataset.csv           ← required by demo.py
secure_dedup_50k.csv  ← required by compare.py
demo.py
compare.py
generate_test_dataset.py
requirements.txt
README.md
```

> ⚠️ **Both CSV files must exist before running any script.** If missing, re-clone the repository.

### Step 4 — Install dependencies

```bash
pip3 install -r requirements.txt
```

### Step 5 — Run the scripts

> All scripts are run directly from the `SDDaaS/` folder

---

### Step 5 — Run the scripts

**▶ Demo (Interactive Web UI)**
```bash
python3 demo.py
```
Browser opens automatically at `http://localhost:8765`

| Mode | Description |
|------|-------------|
| **Single file upload** | Shows full 3-step pipeline: Tree Routing → Bloom Filter check → Metadata exact-match |
| **Batch upload** | Hashes multiple files instantly, sends JSON to server, BF checks all at once |
| **Simulate 5,000 files** | Auto-generates 4,500 unique + 500 duplicates in-memory, no real files needed |

---

**▶ Comparison Benchmark (Graphs)**
```bash
python3 compare.py
```
Prints 4 comparison tables and saves **`sddaas_v8.png`**

| Graph | Metric | Systems |
|-------|--------|---------|
| Graph 1 | Search Latency (µs) — lower is better | 6 systems |
| Graph 2 | Storage Used (MB) — lower is better | 4 systems |
| Graph 3 | False Positive Rate (%) — lower is better | 4 systems |
| Graph 4 | Storage Saved (%) vs Duplication Ratio — higher is better | 4 systems |

Baseline systems simulated inside `compare.py`:

| Class | Paper |
|-------|-------|
| `Li2016` | Differential Bloom Filter — IEEE ICSESS 2016 |
| `Douceur2002` | SALAD: CE + DHT — IEEE ICDCS 2002 |
| `Xiong2019` | SRRS: Role-Authorized Tree + DCF — IEEE Access 2019 |
| `TSCF2021` | Two-Stage Cuckoo Filter — IEEE MSN 2021 |
| `FCDedup2023` | Fog+Cloud Two-Level Dedup — IEEE TPDS 2023 |

---

**▶ Generate Unique Test Dataset (optional)**
```bash
python3 generate_test_dataset.py
```
Creates `Unique_Test_Dataset/` folder with 5,000 unique PDF files (each with a unique SHA-256 hash) to verify the engine detects zero false duplicates on a fully unique dataset.

---

## 🔬 System Architecture

```
Upload Request
     │
     ▼
[3-Level Search Tree]
  Root → Dept (D01–D20) → Emp (E00001–E50000) → FileType (10 types)
     │
     ▼
[Bloom Filter @ Leaf]
  capacity = 2 × avg_items | FPR target = 1%
  items/leaf ≈ 1.5  →  actual FPR ≈ near-zero
     │
  BF miss → NEW FILE → insert BF + Metadata → store ciphertext
  BF hit  → check Metadata
               │
               exact match → DUPLICATE → store 64-byte reference only
               no match    → false positive → treat as new
```

---

## 📈 Benchmark Results (N = 500,000 files, dup_ratio = 15.05%)

| System | Latency (µs) | FPR (%) | Storage (MB) | Dedup Eff. |
|--------|:-----------:|:-------:|:------------:|:----------:|
| **SDDaaS (Proposed)** | **1.221** | **0.000085** | 2,187,580 | **15.05%** |
| Li 2016 (Diff-BF) | 5.052 | 1.276 | 2,286,350 | 6.77% |
| Douceur 2002 (SALAD) | 6.572 | 0.000 | 2,143,940 | 12.58% |
| Xiong 2019 (SRRS) | 9.175 | 1.000 | 2,094,390 | 14.60% |
| TSCF 2021 (Two-Stage CF) | 4.194 | 0.195 | — | — |
| FCDedup 2023 (Fog+Cloud) | 392.025 | 0.000 | — | — |

SDDaaS achieves **the lowest latency** and **highest dedup efficiency** simultaneously. The only tradeoff is ~4.4% higher raw storage than Xiong 2019, which is the justified cost of CP-ABE key protection and per-partition BF metadata.

---

## 📦 Requirements

```
pandas>=1.5.0
matplotlib>=3.5.0
tabulate>=0.9.0
```

See `requirements.txt` for exact versions.

---

## 📄 Related Works (simulated in `experiments/compare.py`)

1. Z. Li et al., "Deduplication of files in cloud storage based on differential bloom filter," IEEE ICSESS 2016.
2. J. R. Douceur et al., "Reclaiming space from duplicate files in a serverless distributed file system," IEEE ICDCS 2002.
3. H. Xiong et al., "SRRS: A Secure and Role-based Role-Authorized Retrieval System for Cloud Deduplication," IEEE Access 2019.
4. J. Liu et al., "Two-Stage Cuckoo Filter for Data Deduplication," IEEE MSN 2021.
5. J. Song et al., "Two-Level Deduplication for Encrypted Data in Fog Computing," IEEE TPDS 2023.
