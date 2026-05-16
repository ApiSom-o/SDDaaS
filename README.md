# SDDaaS — Secure Deduplication as a Service

> **Bloom Filter–based Secure Deduplication System for Cloud Storage**
> A research prototype implementing hierarchical Bloom Filter deduplication with CP-ABE encryption support.

---

## 📌 Project Overview

**SDDaaS** (Secure Deduplication as a Service) is a cloud-storage deduplication system that uses a **3-level hierarchical Search Tree** (Department → Employee → File Type) combined with **Bloom Filters** to detect duplicate files with near-zero false positive rates while preserving data privacy through Convergent Encryption (CE) and CP-ABE overhead modeling.

### Key Features
- ✅ **O(1) 3-level tree routing** — no full scan across 50,000 clients
- ✅ **Partitioned Bloom Filters** — 500,000 independent BF partitions (50K clients × 10 file types)
- ✅ **Near-zero FPR** — items/leaf ≈ 1.5, far below BF capacity
- ✅ **Instant batch deduplication** — browser pre-hashes via SubtleCrypto, sends JSON only
- ✅ **Interactive web demo** — built-in HTTP server, no external framework required

---

## 📁 Repository Structure

```
SDDaaS/
├── demo.py                  # Web demo server (SDDaaS engine + HTTP UI)
├── compare.py               # Performance comparison vs 5 related works
├── generate_test_dataset.py # Script to generate 5,000 unique test PDF files
├── dataset.csv              # Dataset 1 — 50,000 employees (Emp_ID, Dept_ID)
├── secure_dedup_50k.csv     # Dataset 2 — 500,000 file upload records
├── requirements.txt         # Python dependencies
└── README.md
```

---

## 📊 Datasets

### `dataset.csv` — Employee Registry
| Column | Description |
|--------|-------------|
| `Emp_ID` | Employee ID (E00001 – E50000) |
| `Dept_ID` | Department ID (D01 – D20) |

- **50,000 employees** across **20 departments**
- Used by `demo.py` to seed the Bloom Filter tree

### `secure_dedup_50k.csv` — File Upload Records
| Column | Description |
|--------|-------------|
| `Emp_ID` | Employee ID |
| `Dept_ID` | Department ID |
| `File_Type` | One of 10 types (PDF, Image, Code, Video, …) |
| `File_Size_KB` | File size in KB |
| `File_Hash` | SHA-256 hash (truncated) |
| `Upload_Date` | Upload timestamp |
| `Is_Duplicate` | Boolean duplicate flag |

- **500,000 records** | Duplicate ratio ≈ **15.05%** | Avg file size ≈ **5,022 KB**
- Used by `compare.py` for benchmark calculations

---

## 🚀 Quick Start

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Run the interactive web demo
```bash
python demo.py
```
Browser will open automatically at `http://localhost:8765`

The demo supports:
- **Single file upload** → shows 3-step Bloom Filter pipeline (routing → BF check → metadata verify)
- **Batch upload** → browser identity-hashes files (filename+size), sends JSON, instant result
- **Simulate 5,000 files** → generates 4,500 unique + 500 duplicates in-memory, no disk read

### 3. Run the comparison benchmark
```bash
python compare.py
```
Generates 4 graphs comparing SDDaaS vs 5 related works across 10K–500K files:
- Graph 1: Search Latency (µs)
- Graph 2: Storage Used (MB)
- Graph 3: False Positive Rate (%)
- Graph 4: Storage Saved (%) vs Duplication Ratio

Output: `sddaas_v8.png`

### 4. Generate unique test dataset (optional)
```bash
python generate_test_dataset.py
```
Creates `Unique_Test_Dataset/` folder with **5,000 unique PDF files** (each has unique content → unique SHA-256 hash). Used to verify the deduplication engine detects zero false duplicates on a truly unique dataset.

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

## 📈 Comparison Benchmarks

Systems compared (at N = 500,000 files, dup_ratio = 15.05%):

| System | Latency (µs) | FPR (%) | Dedup Eff. |
|--------|-------------|---------|------------|
| **SDDaaS (Proposed)** | **~0.87** | **~0.000%** | **15.05%** |
| Li 2016 (Diff-BF) | ~4.8 | ~0.98% | 6.77% |
| Douceur 2002 (SALAD) | ~4.2 | 0.00% | 12.52% |
| Xiong 2019 (SRRS) | ~12.3 | 1.00% | 14.60% |
| TSCF 2021 (Two-Stage CF) | ~3.8 | 0.20% | 15.02% |
| FCDedup 2023 (Fog+Cloud) | ~322 | 0.00% | 15.05% |

---

## 📦 Requirements

```
pandas>=1.5.0
matplotlib>=3.5.0
tabulate>=0.9.0
```

See `requirements.txt` for exact versions.

---

## 📄 References

1. Li et al. (2016). *Differential Bloom Filter for Secure Deduplication.* IEEE ICSESS 2016.
2. Douceur et al. (2002). *Reclaiming Space from Duplicate Files in a Serverless Distributed File System (SALAD).* IEEE ICDCS 2002.
3. Xiong et al. (2019). *Secure Role-based Re-encryption Storage (SRRS).* IEEE Access 2019.
4. Two-Stage Cuckoo Filter (TSCF). IEEE MSN 2021.
5. FCDedup — Two-Level Fog+Cloud Deduplication. IEEE TPDS 2023.

---

## 👤 Author

**ApiSom** — Computer Engineering / Information Technology Research
