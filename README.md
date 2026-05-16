# SDDaaS — Secure Data Deduplication-as-a-Service Using Search Tree and Bloom Filter

> **CSS451-454 Term Project |SIIT, Thammasat University**

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

**SDDaaS** (Secure Data Deduplication-as-a-Service) is a cloud-storage deduplication system that uses a **3-level Search Tree** (Department → Client → File Type) combined with **partitioned Bloom Filters** to detect duplicate files with near-zero false positive rates, while preserving data privacy through Convergent Encryption (CE) and CP-ABE access control.

### Key Features
- ✅ **O(1) 3-level tree routing** 
- ✅ **500,000 partitioned Bloom Filters** 
- ✅ **3-layer false positive mitigation** 
- ✅ **Near-zero FPR** 
- ✅ **Interactive web demo** 

---

## 📁 Repository Structure

```
SDDaaS/
├── README.md
├── requirements.txt
├── demo.py                  # Web demo server 
├── compare.py               # Benchmark comparison vs 5 related works 
├── generate_test_dataset.py # Generates 5,000 unique PDF test files
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
| `File_Type` | Database, Image, PDF, Document, SourceCode, Archive, Presentation, Spreadsheet, Log, Video |
| `File_Size_KB` | File size in KB |
| `File_Hash` | SHA-256 hash (truncated) |
| `Upload_Date` | Upload timestamp |
| `Is_Duplicate` | Boolean duplicate flag |

- **500,000 records** | Duplicate ratio ≈ **15.05%** | Avg file size ≈ **5,022 KB**
- Used by `compare.py` for all benchmark calculations

---

## ⚙️ Requirements

- Python **3.9+**
- macOS (for `compare.py` — uses `matplotlib MacOSX` backend and `open` command)
- `demo.py` works on any OS

Install dependencies:

```bash
pip3 install -r requirements.txt
```

---

## 🚀 How to Run

### Step 1 — Clone the repository

```bash
git clone https://github.com/ApiSom-o/SDDaaS.git
```

### Step 2 — Enter the project folder

```bash
cd SDDaaS
```

### Step 3 — Verify datasets are present

```bash
ls
```

You should see both `dataset.csv` and `secure_dedup_50k.csv` in the folder.
If missing, re-clone the repository.

### Step 4 — Install dependencies

```bash
pip3 install -r requirements.txt
```

### Step 5 — Generate unique test dataset

> ⚠️ **Run this before running `demo.py`**

```bash
python3 generate_test_dataset.py
```

Creates `Unique_Test_Dataset/` folder with **5,000 unique PDF files**, each with a unique SHA-256 hash. Use these files in the demo to verify zero false duplicates on a fully unique dataset.

---

### Step 6 — Run the scripts

**▶ Demo — Interactive Web UI**

```bash
python3 demo.py
```

Browser opens automatically at `http://localhost:8765`

| Mode | Description |
|------|-------------|
| **Single file upload** | Shows full 3-step pipeline: Tree Routing → Bloom Filter → Metadata exact-match |
| **Batch upload** | Hashes multiple files instantly (filename + size), sends JSON to server, BF checks all at once |

> 💡 You can drag files from `Unique_Test_Dataset/` into the demo to test with real unique files.

---

**▶ Comparison Benchmark — 4 Graphs**

```bash
python3 compare.py
```

- Loads `secure_dedup_50k.csv` automatically from the same folder — no need to `cd` first
- Prints 4 comparison tables to the terminal
- Saves **`sddaas_graph.png`** in the same folder as `compare.py`
- Opens the graph automatically after saving

> ⚠️ **Windows users:** `compare.py` uses `matplotlib.use('MacOSX')` and `subprocess.run(["open", ...])` which are macOS-specific. Before running, open `compare.py` and make these two changes:
>
> **Change 1** — find this line near the top:
> ```python
> matplotlib.use('MacOSX')
> ```
> Replace with:
> ```python
> matplotlib.use('Agg')
> ```
>
> **Change 2** — find this line near the bottom:
> ```python
> subprocess.run(["open", out_path])
> ```
> Replace with:
> ```python
> os.startfile(out_path)
> ```
>
> Then run normally. The graph will be saved as `sddaas_graph.png` and open automatically.

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

## 📈 Benchmark Results (N = 500,000 files, dup_ratio = 15.05%)

| System | Latency (µs) | FPR (%) | Storage (MB) | Dedup Eff. |
|--------|:-----------:|:-------:|:------------:|:----------:|
| **SDDaaS (Proposed)** | **1.221** | **0.000085** | 2,187,580 | **15.05%** |
| Li 2016 (Diff-BF) | 5.052 | 1.276 | 2,286,350 | 6.77% |
| Douceur 2002 (SALAD) | 6.572 | 0.000 | 2,143,940 | 12.58% |
| Xiong 2019 (SRRS) | 9.175 | 1.000 | 2,094,390 | 14.60% |
| TSCF 2021 (Two-Stage CF) | 4.194 | 0.195 | — | — |
| FCDedup 2023 (Fog+Cloud) | 392.025 | 0.000 | — | — |

---

## 📄 Related Works (simulated in `compare.py`)

1. Z. Li et al., "Deduplication of files in cloud storage based on differential bloom filter," IEEE ICSESS 2016.
2. J. R. Douceur et al., "Reclaiming space from duplicate files in a serverless distributed file system," IEEE ICDCS 2002.
3. H. Xiong et al., "SRRS: A Secure and Role-based Role-Authorized Retrieval System for Cloud Deduplication," IEEE Access 2019.
4. J. Liu et al., "Two-Stage Cuckoo Filter for Data Deduplication," IEEE MSN 2021.
5. J. Song et al., "Two-Level Deduplication for Encrypted Data in Fog Computing," IEEE TPDS 2023.
