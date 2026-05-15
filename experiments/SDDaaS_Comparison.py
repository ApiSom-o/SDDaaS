import math
import pandas as pd
import matplotlib
import matplotlib.pyplot as plt
# ── Auto-select backend (works on Mac, Linux, Windows, headless servers) ──
try:
    plt.switch_backend("TkAgg")
except Exception:
    try:
        plt.switch_backend("Qt5Agg")
    except Exception:
        matplotlib.use("Agg")  # fallback: บันทึกไฟล์โดยไม่เปิด window
import matplotlib.gridspec as gridspec
from tabulate import tabulate
from abc import ABC, abstractmethod

# ════════════════════════════════════════════════════════════════════════════
#  SECTION 1 : LOAD DATASET
# ════════════════════════════════════════════════════════════════════════════

import os as _os
_HERE = _os.path.dirname(_os.path.abspath(__file__))
_CSV  = _os.path.join(_HERE, "..", "data", "secure_dedup_50k.csv")
df = pd.read_csv(_CSV)

N_CLIENTS_FIXED  = df["Emp_ID"].nunique()       # จำนวน client ทั้งหมด = 50,000
N_DEPTS          = df["Dept_ID"].nunique()       # จำนวน department = 20
N_FILETYPES      = df["File_Type"].nunique()     # จำนวนประเภทไฟล์ = 10
FILE_TYPES       = sorted(df["File_Type"].unique())

DEPT_SIZES       = df.groupby("Dept_ID")["Emp_ID"].nunique().sort_index()
DEPT_DIST        = DEPT_SIZES / DEPT_SIZES.sum() # สัดส่วนขนาดของแต่ละ dept

# ค่าพารามิเตอร์จาก dataset 
DUP_RATIO        = round(df["Is_Duplicate"].mean(), 4)    # อัตราซ้ำ = 0.1505 (15.05%)
AVG_FILE_SIZE_KB = round(df["File_Size_KB"].mean(), 1)    # ขนาดไฟล์เฉลี่ย = 5022.5 KB
N_BF_PARTITIONS  = N_CLIENTS_FIXED * N_FILETYPES          # จำนวน BF partition ทั้งหมด = 500,000

# ค่าเฉลี่ย item ต่อ leaf BF (ใช้คำนวณ FPR ของ SDDaaS)
_fpct            = df.groupby(["Emp_ID", "File_Type"]).size()
AVG_ITEMS_LEAF   = round(_fpct.mean(), 3)                 # ≈ 1.535 items/leaf

print(f"[Dataset] {len(df):,} records | {N_CLIENTS_FIXED:,} clients | "
      f"{N_DEPTS} depts | {N_FILETYPES} file types")
print(f"[Dataset] DUP_RATIO={DUP_RATIO:.4f} ({DUP_RATIO*100:.2f}%) | "
      f"AVG_FILE_SIZE={AVG_FILE_SIZE_KB:.0f} KB | "
      f"BF_PARTITIONS={N_BF_PARTITIONS:,}")

# ขนาด N ที่ใช้ทดสอบ (10K–500K); 500K = ขนาดเต็มของ dataset
N_SIZES = [10_000, 50_000, 100_000, 200_000, 350_000, 500_000]

# ════════════════════════════════════════════════════════════════════════════
#  SECTION 2 : GLOBAL PARAMETERS
#  ค่าคงที่ที่ใช้ร่วมกันทุกระบบ (อ้างอิงจาก paper แต่ละฉบับ)
# ════════════════════════════════════════════════════════════════════════════

FPR_TARGET          = 0.01    # เป้า FPR ของ Bloom Filter = 1%
CPABE_OVERHEAD_FRAC = 0.05    # overhead จาก CP-ABE encryption ≈ 5%
CE_KEY_BYTES        = 256     # ขนาด Convergent Encryption key (256 bytes)
DUP_REF_BYTES       = 64      # ขนาด reference record สำหรับไฟล์ซ้ำ (64 bytes)

# ════════════════════════════════════════════════════════════════════════════
#  SECTION 3 : UTILITIES
#  ฟังก์ชันคำนวณพารามิเตอร์ Bloom Filter
# ════════════════════════════════════════════════════════════════════════════

def bf_params(n_items: int) -> tuple[int, int]:
    """
    คำนวณ m (จำนวน bits) และ k (จำนวน hash functions) ของ BF
    """
    cap = max(n_items, 1) # ป้องกัน n=0 ที่ทำให้ log(0) ไม่ได้
    m   = math.ceil(-cap * math.log(FPR_TARGET) / (math.log(2) ** 2)) # จำนวน bits ที่ต้องใช้สำหรับ n_items และ FPR_TARGET
    k   = max(1, math.ceil((m / cap) * math.log(2))) # จำนวน hash functions ที่เหมาะสมสำหรับ m bits และ n_items
    return m, k

def bf_fpr_actual(n_ins: float, m: int, k: int) -> float:
    """
    FPR ของ BF เมื่อใส่ n_ins items ลงใน BF ขนาด m bits ใช้ k hash
    """
    return min((1 - math.exp(-k * max(n_ins, 0.001) / m)) ** k, 1.0)

def cf_fpr(f_bits: int = 12, b_slots: int = 4) -> float:
    """
    FPR อ้างอิงจาก TSCF 2021
    """
    return (2 * b_slots) / (2 ** f_bits)

# ════════════════════════════════════════════════════════════════════════════
#  SECTION 4 : SDDaaS  (Proposed) 
# ════════════════════════════════════════════════════════════════════════════

class SDDaaS:
    NAME   = "SDDaaS (Proposed)"
    COLOR  = "#2980B9"
    MARKER = "D"

    @staticmethod
    def _items_per_leaf(n_files: int) -> float:
        """จำนวน item เฉลี่ยต่อ leaf BF = N / จำนวน partition ทั้งหมด"""
        return max(n_files / N_BF_PARTITIONS, 0.001)

    @classmethod
    def _leaf_bf_params(cls, n_files: int) -> tuple[int, int]:
        """
        สร้าง BF ที่ capacity = 2× items (margin กันล้น) อย่างน้อย cap = 5 เพื่อเลี่ยง BF ที่เล็กเกิน
        """
        items = cls._items_per_leaf(n_files)
        cap   = max(int(math.ceil(items) * 2), 5)
        return bf_params(cap)

    @classmethod
    def compute_fpr(cls, n_files: int) -> float:
        """
        FPR ของ SDDaaS ≈ near-zero เพราะ items/leaf น้อยมาก (≈1.535)
        เทียบกับ BF ที่ออกแบบรองรับ capacity สูงกว่า
        """
        items = cls._items_per_leaf(n_files)
        m, k  = cls._leaf_bf_params(n_files)
        return bf_fpr_actual(items, m, k)

    @classmethod
    def storage_saved_pct(cls, dup_ratio: float) -> float:
        """
        % storage ที่ประหยัดได้ ≈ dup_ratio หักด้วย FPR penalty 
        @ dup=15.05%: ≈ 15.05%
        """
        fpr_penalty = cls.compute_fpr(500_000) * 0.01
        return max(dup_ratio - fpr_penalty, 0.0) * 100

    @classmethod
    def compute_dedup_eff(cls, n_files: int) -> float:
        """Dedup efficiency = storage_saved_pct / 100 (constant, independent of N)"""
        return cls.storage_saved_pct(DUP_RATIO) / 100

    @classmethod
    def compute_storage_mb(cls, n_files: int) -> float:
        """
        Storage (MB):
          - data_mb   = unique files × size × (1 + CP-ABE overhead)
          - key_mb    = CE key ต่อ unique file
          - ref_mb    = duplicate ref records
          - bf_mb     = BF bits ทุก partition
        """
        n_unique  = int(n_files * (1 - DUP_RATIO))
        n_dup     = n_files - n_unique
        data_mb   = (n_unique * AVG_FILE_SIZE_KB * (1 + CPABE_OVERHEAD_FRAC)) / 1024
        key_mb    = (n_unique * CE_KEY_BYTES) / (1024 * 1024)
        ref_mb    = (n_dup * DUP_REF_BYTES) / (1024 * 1024)
        m_leaf, _ = cls._leaf_bf_params(n_files)
        bf_mb     = (m_leaf * N_BF_PARTITIONS) / (8 * 1024 * 1024)
        return data_mb + key_mb + ref_mb + bf_mb

    @classmethod
    def compute_latency_us(cls, n_files: int) -> float:
        """
        Latency รวม (µs) — O(1) constant เพราะ hierarchical 3 ระดับ:
          dept_us + emp_us + ft_us = 3-level tree lookup (ไม่ขึ้นกับ N)
          bf_us  = BF query ที่ leaf (k hash lookups)
          meta_us = FPR penalty + metadata fetch
        """
        _, k    = cls._leaf_bf_params(n_files)
        fpr     = cls.compute_fpr(n_files)
        dept_us = 0.10                                                  # dept-level lookup
        emp_us  = 0.10 + 0.001 * math.log2(max(N_CLIENTS_FIXED / N_DEPTS, 2))  # emp-level (log ขนาด dept)
        ft_us   = 0.05                                                  # file-type lookup
        bf_us   = 0.30 + k * 0.08                                      # BF query
        meta_us = fpr * 0.15 + 0.10                                    # metadata + FP handling
        return dept_us + emp_us + ft_us + bf_us + meta_us


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 5 : COMPARISON SYSTEMS
# ════════════════════════════════════════════════════════════════════════════

class ComparisonSystem(ABC):
    NAME = ""; COLOR = "#888888"; MARKER = "o"

    @classmethod
    @abstractmethod
    def compute_fpr(cls, n: int) -> float: ...
    @classmethod
    @abstractmethod
    def compute_storage_mb(cls, n: int) -> float: ...
    @classmethod
    @abstractmethod
    def compute_latency_us(cls, n: int) -> float: ...
    @classmethod
    @abstractmethod
    def storage_saved_pct(cls, dup_ratio: float) -> float: ...

    @classmethod
    def compute_dedup_eff(cls, n: int) -> float:
        """Dedup efficiency จาก storage_saved_pct """
        return cls.storage_saved_pct(DUP_RATIO) / 100


# ── Li 2016 (Differential Bloom Filter, IEEE ICSESS 2016) ─────────────────
class Li2016(ComparisonSystem):
    """
    แบ่ง BF เป็น 2 ส่วน: Static Content (SC) 87% + Content-Defined Chunking (CDC) 13%
    น้ำหนักแต่ละส่วน ∝ √(n × cost) → ให้ bits มากขึ้นแก่ส่วนที่แพงกว่า
    Max dedup efficiency ≈ 18% (จาก Fig.3 ของ paper)
    @ dup=15.05%: save = min(0.1505 × 0.45, 0.18) × 100 = 6.77%
    """
    NAME    = "Li 2016 (Diff-BF)"; COLOR = "#E07B39"; MARKER = "o"
    SC_FRAC = 0.87   # สัดส่วนไฟล์ static content
    CDC_FRAC = 0.13  # สัดส่วนไฟล์ content-defined chunking
    Cs = 16.0        # relative cost ของ SC lookup
    Cc = 1.0         # relative cost ของ CDC lookup
    MAX_EFF = 0.18   # cap จาก Fig.3 ของ paper

    @classmethod
    def _split_bf(cls, n: int):
        """
        แบ่ง m_total ระหว่าง BF_SC กับ BF_CDC ตามน้ำหนัก w เพื่อให้ FPR ของแต่ละส่วนสมดุลกับ latency cost
        """
        n_sc  = max(int(n * cls.SC_FRAC), 1)
        n_cdc = max(int(n * cls.CDC_FRAC), 1)
        m_tot = math.ceil(-n * math.log(FPR_TARGET) / (math.log(2) ** 2))
        wsc   = math.sqrt(n_sc  * cls.Cs)
        wcdc  = math.sqrt(n_cdc * cls.Cc)
        tw    = wsc + wcdc
        m1    = max(int(m_tot * wsc  / tw), 1)
        m2    = max(int(m_tot * wcdc / tw), 1)
        k1    = max(1, round((m1 / n_sc)  * math.log(2)))
        k2    = max(1, round((m2 / n_cdc) * math.log(2)))
        return n_sc, n_cdc, m1, m2, k1, k2

    @classmethod
    def compute_fpr(cls, n: int) -> float:
        """FPR รวม = weighted average ของ FPR_SC + FPR_CDC"""
        n_sc, n_cdc, m1, m2, k1, k2 = cls._split_bf(n)
        return (cls.SC_FRAC  * bf_fpr_actual(n_sc,  m1, k1) +
                cls.CDC_FRAC * bf_fpr_actual(n_cdc, m2, k2))

    @classmethod
    def storage_saved_pct(cls, dup_ratio: float) -> float:
        """
        Algorithm detect ได้ 45% ของ dup @ dup=15.05%
        """
        return min(dup_ratio * 0.45, cls.MAX_EFF) * 100

    @classmethod
    def compute_storage_mb(cls, n: int) -> float:
        eff      = cls.compute_dedup_eff(n)
        n_stored = int(n * (1 - eff))
        data_mb  = (n_stored * AVG_FILE_SIZE_KB) / 1024
        m_tot    = math.ceil(-n * math.log(FPR_TARGET) / (math.log(2) ** 2))
        bf_mb    = m_tot / (8 * 1024 * 1024)
        index_mb = (n * 100) / (1024 * 1024)
        return data_mb + bf_mb + index_mb

    @classmethod
    def compute_latency_us(cls, n: int) -> float:
        n_sc, n_cdc, m1, m2, k1, k2 = cls._split_bf(n)
        bf_us = 0.80 + k1 * 0.12
        ht_us = 0.50 + 0.15 * math.log2(max(n, 2))
        scan  = cls.compute_fpr(n) * math.sqrt(max(n, 1)) * 0.008
        return bf_us + ht_us + scan


# ── Douceur 2002 (CE + SALAD, IEEE ICDCS 2002) ───────────────────────────
class Douceur2002(ComparisonSystem):
    """
    Convergent Encryption + SALAD (Secure And Liability-free Assured Dedup)
    ใช้ DHT D=2 ระดับ, λ=2.5 → P_loss = D·e^{-λ} ≈ 16.4%
    Storage saved = dup_ratio × (1 - P_loss)
    @ dup=15.05%
    """
    NAME      = "Douceur 2002 (SALAD)"; COLOR = "#27AE60"; MARKER = "^"
    D         = 2      # จำนวนระดับของ DHT
    LAMBDA    = 2.5    # พารามิเตอร์ SALAD (ควบคุม redundancy)
    REC_BYTES = 36     # ขนาด SALAD record ต่อ item

    @classmethod
    def _ploss(cls) -> float:
        """ความน่าจะเป็นที่ dedup จะพลาด = D·e^{-λ}"""
        return cls.D * math.exp(-cls.LAMBDA)

    @classmethod
    def compute_fpr(cls, n: int) -> float:
        """CE ไม่มี false positive (hash-based exact match)"""
        return 0.0

    @classmethod
    def storage_saved_pct(cls, dup_ratio: float) -> float:
        return dup_ratio * (1 - cls._ploss()) * 100

    @classmethod
    def compute_storage_mb(cls, n: int) -> float:
        """data storage + SALAD routing table (λ records ต่อ item × D levels)"""
        eff      = cls.compute_dedup_eff(n)
        n_unique = int(n * (1 - eff))
        data_mb  = (n_unique * AVG_FILE_SIZE_KB) / 1024
        salad_mb = (n * cls.REC_BYTES * cls.LAMBDA) / (1024 * 1024)
        return data_mb + salad_mb

    @classmethod
    def compute_latency_us(cls, n: int) -> float:
        """
        SHA hash + DHT hop latency
        leaf table size ≈ (N/λ)^{1/D} hop cost เพิ่มแบบ sublinear
        """
        sha_us     = 1.50
        leaf_table = cls.D * cls.LAMBDA * max(1.0, (n / cls.LAMBDA) ** (1.0 / cls.D))
        hop_us     = cls.D * (0.20 + 0.001 * leaf_table)
        return sha_us + hop_us + 0.20


# ── Xiong 2019 (SRRS: CE + Role Re-enc + RAT + DCF, IEEE Access 2019) ────
class Xiong2019(ComparisonSystem):
    """
    Secure Role-based Re-encryption Storage (SRRS)
    ใช้ Role Attribute Tree (RAT) ความลึก log_3(N) + Dynamic Cuckoo Filter (DCF)
    DCF FPR = 1% (constant), penalty = 3% จาก false positive re-encryption
    Storage saved = dup_ratio × (1 - 0.03)
    @ dup=15.05%
    """
    NAME      = "Xiong 2019 (SRRS)"; COLOR = "#9B59B6"; MARKER = "s"
    RAT_ORDER = 3      # branching factor ของ Role Attribute Tree
    DCF_FPR   = 0.01   # DCF false positive rate = 1%
    RKEY_BITS = 256    # re-encryption key size
    PENALTY   = 0.03   # efficiency penalty จาก FP re-encryption

    @classmethod
    def compute_fpr(cls, n: int) -> float:
        """DCF FPR คงที่ = 1% ไม่ขึ้นกับ N"""
        return cls.DCF_FPR

    @classmethod
    def storage_saved_pct(cls, dup_ratio: float) -> float:
        return max(dup_ratio * (1 - cls.PENALTY), 0.0) * 100

    @classmethod
    def compute_storage_mb(cls, n: int) -> float:
        """data + re-encryption keys (unique files) + DCF structure (1.5× BF bits)"""
        eff      = cls.compute_dedup_eff(n)
        n_unique = int(n * (1 - eff))
        data_mb  = (n_unique * AVG_FILE_SIZE_KB) / 1024
        rkey_mb  = (n_unique * (cls.RKEY_BITS // 8)) / (1024 * 1024)
        dcf_bits = math.ceil(-n * math.log(cls.DCF_FPR) / (math.log(2) ** 2)) * 1.5
        dcf_mb   = dcf_bits / (8 * 1024 * 1024)
        return data_mb + rkey_mb + dcf_mb

    @classmethod
    def compute_latency_us(cls, n: int) -> float:
        """
        RAT traversal O(log_3 N) + DCF query + SHA + re-encryption
        false positive penalty ∝ DCF_FPR × √N
        """
        rat_h    = max(1, math.ceil(math.log(max(n, 2)) / math.log(cls.RAT_ORDER)))
        rat_us   = rat_h * cls.RAT_ORDER * 0.15 + 0.10
        _, k_dcf = bf_params(max(int(n * 0.75), 1))
        dcf_us   = 0.30 + k_dcf * 0.10
        sha_us   = 1.50
        reenc_us = 0.20 + 0.05 * math.log2(max(n, 2))
        fp_pen   = cls.DCF_FPR * math.sqrt(max(n, 1)) * 0.004
        return rat_us + dcf_us + sha_us + reenc_us + fp_pen


# ── TSCF 2021 (Two-Stage Cuckoo Filter, IEEE MSN 2021) ───────────────────
class TSCF2021(ComparisonSystem):
    """
    Two-Stage Cuckoo Filter: Stage-1 CF (coarse) → Stage-2 CF (fine)
    FPR คงที่ ≈ 0.195% (f=12 bits, b=4 slots)
    """
    NAME   = "TSCF 2021 (Two-Stage CF)"; COLOR = "#C0392B"; MARKER = "P"
    CF_F   = 12  # fingerprint bits
    CF_B   = 4   # slots per bucket

    @classmethod
    def compute_fpr(cls, n: int) -> float:
        return cf_fpr(cls.CF_F, cls.CF_B)  # = 2×4 / 2^12 ≈ 0.00195

    @classmethod
    def storage_saved_pct(cls, dup_ratio: float) -> float:
        """FPR ต่ำมาก → penalty เล็กน้อย"""
        return max(dup_ratio * (1 - cf_fpr(cls.CF_F, cls.CF_B) * 0.02), 0.0) * 100

    @classmethod
    def compute_storage_mb(cls, n: int) -> float:
        """data + CF bucket storage (m_buckets × b slots × f bits)"""
        eff       = cls.compute_dedup_eff(n)
        n_unique  = int(n * (1 - eff))
        data_mb   = (n_unique * AVG_FILE_SIZE_KB) / 1024
        m_buckets = math.ceil(n / (cls.CF_B * 0.95))  # load factor 95%
        cf_mb     = (m_buckets * cls.CF_B * cls.CF_F) / (8 * 1024 * 1024)
        return data_mb + cf_mb

    @classmethod
    def compute_latency_us(cls, n: int) -> float:
        """SHA hash + 2-stage CF lookup O(1) + index O(log N) + FP penalty"""
        sha_us   = 1.50
        cf_us    = 0.25 + 2 * (cls.CF_B * 0.03)   # 2 stages
        index_us = 0.30 + 0.10 * math.log2(max(n, 2))
        fp_pen   = cls.compute_fpr(n) * 0.008 * math.sqrt(max(n, 1))
        return sha_us + cf_us + index_us + fp_pen


# ── FCDedup 2023 (Two-Level Fog+Cloud Dedup, IEEE TPDS 2023) ─────────────
class FCDedup2023(ComparisonSystem):
    """
    Two-Level Dedup: Fog node (edge dedup) + Cloud (global dedup)
    ใช้ short hash (10-bit) แบ่ง namespace  N_collisions = N / 2^10
    FPR = exact hash matching latency เพิ่มแบบ linear ตาม N_collisions
    """
    NAME            = "FCDedup 2023 (Fog+Cloud)"; COLOR = "#795548"; MARKER = "X"
    SHORT_HASH_BITS = 10  # bits ของ short hash สำหรับ namespace partition
    N_FOG           = 4   # จำนวน fog node

    @classmethod
    def _nocn(cls, n: int) -> float:
        """จำนวน collision เฉลี่ยต่อ namespace bucket = N / 2^BITS"""
        return max(1.0, n / (2 ** cls.SHORT_HASH_BITS))

    @classmethod
    def compute_fpr(cls, n: int) -> float:
        """Exact matching → ไม่มี false positive"""
        return 0.0

    @classmethod
    def storage_saved_pct(cls, dup_ratio: float) -> float:
        """Dedup สมบูรณ์ = dup_ratio เต็มๆ"""
        return dup_ratio * 100

    @classmethod
    def compute_storage_mb(cls, n: int) -> float:
        """data + cloud metadata (100 bytes/file) + fog metadata (64 bytes × N_FOG)"""
        eff           = cls.compute_dedup_eff(n)
        n_unique      = int(n * (1 - eff))
        data_mb       = (n_unique * AVG_FILE_SIZE_KB) / 1024
        cloud_meta_mb = (n * 100) / (1024 * 1024)
        fog_meta_mb   = (n * 64 * cls.N_FOG) / (1024 * 1024)
        return data_mb + cloud_meta_mb + fog_meta_mb

    @classmethod
    def compute_latency_us(cls, n: int) -> float:
        """
        Latency เพิ่มแบบ O(N_collision) เพราะต้อง scan bucket เมื่อ collision สูง
        fog lookup + cloud lookup + collision scan + verification
        """
        nocn = cls._nocn(n)
        return 0.60 + 0.80 + nocn * 0.65 + nocn * 0.15  # linear ใน N_collisions


# ════════════════════════════════════════════════════════════════════════════
#  SECTION 6 : REGISTER
# ════════════════════════════════════════════════════════════════════════════

SYSTEMS_6 = {
    "li2016"    : Li2016,
    "douceur"   : Douceur2002,
    "xiong2019" : Xiong2019,
    "tscf2021"  : TSCF2021,
    "fcdedup"   : FCDedup2023,
}
SYSTEMS_4 = {
    "li2016"    : Li2016,
    "douceur"   : Douceur2002,
    "xiong2019" : Xiong2019,
}

# ════════════════════════════════════════════════════════════════════════════
#  SECTION 7 : COMPUTE RESULTS
# ════════════════════════════════════════════════════════════════════════════

NS      = N_SIZES
METRICS = ("fpr", "storage_mb", "latency_us", "dedup_eff")

def _scale(m: str, v: float) -> float:
    """แปลง fpr / dedup_eff เป็น % """
    return v * 100 if m in ("fpr", "dedup_eff") else v

def build_results(cls_or_dict):
    if isinstance(cls_or_dict, dict):
        return {
            key: {m: [_scale(m, getattr(Cls, f"compute_{m}")(n)) for n in NS]
                  for m in METRICS}
            for key, Cls in cls_or_dict.items()
        }
    Cls = cls_or_dict
    return {m: [_scale(m, getattr(Cls, f"compute_{m}")(n)) for n in NS]
            for m in METRICS}

res_sd = build_results(SDDaaS)
res_6  = build_results(SYSTEMS_6)
res_4  = build_results(SYSTEMS_4)

# รวมทุกระบบไว้ใน data dict 
SYS_CLS = {
    "li2016"    : Li2016,
    "douceur"   : Douceur2002,
    "xiong2019" : Xiong2019,
    "tscf2021"  : TSCF2021,
    "fcdedup"   : FCDedup2023,
}
data = {key: build_results(Cls) for key, Cls in SYS_CLS.items()}
data["sddaas"] = res_sd

# ════════════════════════════════════════════════════════════════════════════
#  SECTION 8 : PRINT TABLES
# ════════════════════════════════════════════════════════════════════════════

def _nf(n): return f"{n//1_000_000}M" if n >= 1_000_000 else f"{n//1_000}K"

def print_table(title, metric, sys_dict, res_dict, fmt, pct):
    hdr  = ["N (files)"] + [sys_dict[k].NAME for k in sys_dict] + [SDDaaS.NAME]
    rows = []
    for i, n in enumerate(NS):
        row = [_nf(n)]
        for key in sys_dict:
            v = res_dict[key][metric][i]
            row.append(f"{v:{fmt}}" + ("%" if pct else ""))
        row.append(f"{res_sd[metric][i]:{fmt}}" + ("%" if pct else ""))
        rows.append(row)
    print(f"\n{'='*115}\n  {title}\n{'='*115}")
    print(tabulate(rows, headers=hdr, tablefmt="grid"))

print_table("TABLE I  — Search Latency (µs)  [lower=better]  | 6 Systems",
            "latency_us", SYSTEMS_6, res_6, ".3f", False)
print_table("TABLE II — Storage (MB)  [lower=better]  | 4 Systems",
            "storage_mb", SYSTEMS_4, res_4, ".1f", False)
print_table("TABLE III— False Positive Rate (%)  [lower=better]  | 4 Systems",
            "fpr", SYSTEMS_4, res_4, ".6f", True)
print_table("TABLE IV — Deduplication Efficiency (%)  [higher=better]  | 4 Systems",
            "dedup_eff", SYSTEMS_4, res_4, ".2f", True)

# ════════════════════════════════════════════════════════════════════════════
#  SECTION 9 : GRAPH 4 DATA
# ════════════════════════════════════════════════════════════════════════════

# DUP_RATIO (0.1505)
DUP_RANGE = sorted(set(
    [x / 100 for x in range(5, 82, 5)] + [DUP_RATIO]
))
dup_pct = [d * 100 for d in DUP_RANGE]

g4 = {
    "sddaas"    : [SDDaaS.storage_saved_pct(d)      for d in DUP_RANGE],
    "li2016"    : [Li2016.storage_saved_pct(d)      for d in DUP_RANGE],
    "douceur"   : [Douceur2002.storage_saved_pct(d) for d in DUP_RANGE],
    "xiong2019" : [Xiong2019.storage_saved_pct(d)   for d in DUP_RANGE],
}

idx_real = DUP_RANGE.index(DUP_RATIO)

# ════════════════════════════════════════════════════════════════════════════
#  CONSISTENCY CHECK
# ════════════════════════════════════════════════════════════════════════════

print(f"\n{'='*70}")
print(f"  CONSISTENCY CHECK — Table IV vs Graph 4 @ DUP={DUP_RATIO*100:.2f}%")
print(f"{'='*70}")
checks = [
    ("SDDaaS",     res_sd["dedup_eff"][0],              g4["sddaas"][idx_real]),
    ("Li 2016",    res_4["li2016"]["dedup_eff"][0],     g4["li2016"][idx_real]),
    ("Douceur",    res_4["douceur"]["dedup_eff"][0],    g4["douceur"][idx_real]),
    ("Xiong 2019", res_4["xiong2019"]["dedup_eff"][0],  g4["xiong2019"][idx_real]),
]
all_match = True
for name, tv, gv in checks:
    match = "✅ MATCH" if abs(tv - gv) < 0.001 else "❌ MISMATCH"
    if "❌" in match: all_match = False
    print(f"  {name:<15} Table={tv:.4f}%   Graph★={gv:.4f}%   {match}")
print(f"\n  {'✅ ALL CONSISTENT' if all_match else '❌ FIX NEEDED'}")
print()

# ════════════════════════════════════════════════════════════════════════════
#  SECTION 10 : PLOT
# ════════════════════════════════════════════════════════════════════════════

XLABEL = "N  (Number of Files Uploaded)"

BBOX = {
    "sd": dict(boxstyle="round,pad=0.35", fc="#EBF5FB", ec=SDDaaS.COLOR,      alpha=0.92),
    "li": dict(boxstyle="round,pad=0.35", fc="#FEF5EC", ec=Li2016.COLOR,       alpha=0.88),
    "dc": dict(boxstyle="round,pad=0.35", fc="#E8F8EE", ec=Douceur2002.COLOR,  alpha=0.88),
    "xi": dict(boxstyle="round,pad=0.35", fc="#F4ECF7", ec=Xiong2019.COLOR,    alpha=0.88),
    "fc": dict(boxstyle="round,pad=0.35", fc="#EFEBE9", ec=FCDedup2023.COLOR,  alpha=0.88),
}
ARR = dict(arrowstyle="->", lw=0.8)

def ax_style(ax, title, ylabel="", xlabel=""):
    ax.set_facecolor("#FFFFFF")
    ax.set_title(title, fontsize=10.5, fontweight="bold", pad=9)
    ax.set_xlabel(xlabel, fontsize=9)
    ax.set_ylabel(ylabel, fontsize=9.5)
    ax.grid(True, alpha=0.28, linestyle="--")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

fig = plt.figure(figsize=(22, 18))
fig.patch.set_facecolor("#F8F9FA")
gs  = gridspec.GridSpec(2, 2, figure=fig,
                        wspace=0.45, hspace=0.68,
                        left=0.07, right=0.97, top=0.96, bottom=0.07)
ax1 = fig.add_subplot(gs[0, 0])
ax2 = fig.add_subplot(gs[0, 1])
ax3 = fig.add_subplot(gs[1, 0])
ax4 = fig.add_subplot(gs[1, 1])

# ── Graph 1: Search Latency (µs) ───────────────────────────────
for k in ["li2016","douceur","xiong2019","tscf2021","fcdedup"]:
    Cls = SYS_CLS[k]
    ax1.plot(NS, data[k]["latency_us"],
             marker=Cls.MARKER, color=Cls.COLOR,
             linewidth=1.8, markersize=5.5, label=Cls.NAME)
ax1.plot(NS, data["sddaas"]["latency_us"],
         marker=SDDaaS.MARKER, color=SDDaaS.COLOR,
         linewidth=2.8, markersize=6.5, label=SDDaaS.NAME)
ax1.set_xscale("log")
ax_style(ax1, "Graph 1 – Search Latency (µs)  [6 Systems,  lower = better]",
         "Latency (µs)", XLABEL)
ax1.legend(fontsize=7.2, loc="upper left", framealpha=0.95)
_sd_lat = data["sddaas"]["latency_us"][-1]
_fc_lat = data["fcdedup"]["latency_us"][-1]
ax1.annotate(
    f"SDDaaS: O(1) × 3 levels\n≈ {_sd_lat:.2f} µs  (flat)",
    xy=(NS[-2], _sd_lat), xytext=(NS[-3]*1.5, _sd_lat + 50),
    fontsize=7.5, color=SDDaaS.COLOR, ha="center",
    arrowprops=dict(**ARR, color=SDDaaS.COLOR), bbox=BBOX["sd"])
ax1.annotate(
    f"FCDedup: O(N) bilinear\n≈ {_fc_lat:.0f} µs @ N=500K",
    xy=(NS[-1], _fc_lat), xytext=(NS[-4]*1.3, _fc_lat*0.82),
    fontsize=7.0, color=FCDedup2023.COLOR, ha="center",
    arrowprops=dict(**ARR, color=FCDedup2023.COLOR), bbox=BBOX["fc"])

# ── Graph 2: Storage Used (MB) ─────────────────────────────────
for k in ["li2016","douceur","xiong2019"]:
    Cls = SYS_CLS[k]
    ax2.plot(NS, data[k]["storage_mb"],
             marker=Cls.MARKER, color=Cls.COLOR,
             linewidth=1.8, markersize=5.5, label=Cls.NAME)
ax2.plot(NS, data["sddaas"]["storage_mb"],
         marker=SDDaaS.MARKER, color=SDDaaS.COLOR,
         linewidth=2.8, markersize=6.5, label=SDDaaS.NAME)
ax2.set_xscale("log")
ax_style(ax2, "Graph 2 – Storage Used (MB)  [4 Systems,  lower = better]",
         "Storage (MB)", XLABEL)
ax2.legend(fontsize=7.5, loc="upper left", framealpha=0.95)
ax2.annotate(
    f"Li 2016: max 18% dedup\n→ highest storage",
    xy=(NS[-2], data["li2016"]["storage_mb"][-2]),
    xytext=(NS[-3]*2.2, data["li2016"]["storage_mb"][-2]*0.52),
    fontsize=7.0, color=Li2016.COLOR, ha="center",
    arrowprops=dict(**ARR, color=Li2016.COLOR), bbox=BBOX["li"])
ax2.annotate(
    f"SDDaaS: {DUP_RATIO*100:.1f}% dedup\n+ {N_BF_PARTITIONS:,} BF partitions",
    xy=(NS[-3], data["sddaas"]["storage_mb"][-3]),
    xytext=(NS[-5]*2.0, data["sddaas"]["storage_mb"][-3]*2.2),
    fontsize=7.0, color=SDDaaS.COLOR, ha="center",
    arrowprops=dict(**ARR, color=SDDaaS.COLOR), bbox=BBOX["sd"])

# ── Graph 3: False Positive Rate (%) ───────────────────────────
for k in ["li2016","douceur","xiong2019"]:
    Cls = SYS_CLS[k]
    ls  = "--" if k == "douceur" else "-" 
    ax3.plot(NS, data[k]["fpr"],
             marker=Cls.MARKER, color=Cls.COLOR,
             linewidth=1.8, markersize=5.5, linestyle=ls, label=Cls.NAME)
ax3.plot(NS, data["sddaas"]["fpr"],
         marker=SDDaaS.MARKER, color=SDDaaS.COLOR,
         linewidth=2.8, markersize=6.5, label=SDDaaS.NAME)
ax3.set_xscale("log")
ax_style(ax3, "Graph 3 – False Positive Rate (%)  [4 Systems,  lower = better]",
         "FPR (%)", XLABEL)
ax3.legend(fontsize=7.5, loc="upper left", framealpha=0.95, bbox_to_anchor=(0.01, 0.72))
ax3.annotate(
    f"SDDaaS: items/leaf = N/{N_BF_PARTITIONS:,}\n→ FPR near-zero",
    xy=(NS[-2], data["sddaas"]["fpr"][-2]),
    xytext=(NS[-4]*1.5, data["sddaas"]["fpr"][-2] + 0.35),
    fontsize=7.0, color=SDDaaS.COLOR, ha="center",
    arrowprops=dict(**ARR, color=SDDaaS.COLOR), bbox=BBOX["sd"])
ax3.annotate(
    f"Li 2016: global BF → FPR grows\n≈{data['li2016']['fpr'][-1]:.2f}% @ N=500K",
    xy=(NS[-1], data["li2016"]["fpr"][-1]),
    xytext=(NS[-3]*1.5, (data["li2016"]["fpr"][-1] + data["xiong2019"]["fpr"][-1]) / 2),
    fontsize=7.0, color=Li2016.COLOR, ha="center",
    arrowprops=dict(**ARR, color=Li2016.COLOR), bbox=BBOX["li"])
ax3.annotate(
    "Xiong 2019: DCF FPR=1%\n(constant)",
    xy=(NS[3], data["xiong2019"]["fpr"][3]),
    xytext=(NS[2]*1.8, data["xiong2019"]["fpr"][3] - 0.28),
    fontsize=7.0, color=Xiong2019.COLOR, ha="center",
    arrowprops=dict(**ARR, color=Xiong2019.COLOR), bbox=BBOX["xi"])

# ── Graph 4: Storage Saved (%) vs Duplication Ratio ─────────────────────
for key, Cls in [("li2016",Li2016),("douceur",Douceur2002),("xiong2019",Xiong2019)]:
    ax4.plot(dup_pct, g4[key],
             marker=Cls.MARKER, color=Cls.COLOR,
             linewidth=1.8, markersize=4, label=Cls.NAME)
ax4.plot(dup_pct, g4["sddaas"],
         marker=SDDaaS.MARKER, color=SDDaaS.COLOR,
         linewidth=2.8, markersize=5, label=SDDaaS.NAME)
ax4.plot(dup_pct, [d * 100 for d in DUP_RANGE],
         color="gray", linestyle=":", linewidth=1.2, alpha=0.6, label="Ideal (100%)")

real_dup_pct = DUP_RATIO * 100
ax4.axvline(x=real_dup_pct, color="#E74C3C", linestyle="--", linewidth=1.2, alpha=0.7)
ax4.text(real_dup_pct + 1.2, 2,
         f"Dataset\n{real_dup_pct:.2f}%",
         fontsize=7.5, color="#E74C3C", fontweight="bold")

for key, color in [("sddaas", SDDaaS.COLOR), ("li2016", Li2016.COLOR),
                   ("douceur", Douceur2002.COLOR), ("xiong2019", Xiong2019.COLOR)]:
    ax4.scatter([real_dup_pct], [g4[key][idx_real]],
                color=color, s=100, zorder=6, marker="*")

ax4.set_ylim(0, 85)
ax_style(ax4,
         "Graph 4 – Storage Saved (%)  [4 Systems,  higher = better]\n",
         xlabel="Duplication Ratio (%)")
ax4.legend(fontsize=7.5, loc="upper left", framealpha=0.95)

ax4.annotate(
    f"SDDaaS ≈ {g4['sddaas'][idx_real]:.2f}%\n= Table IV ★",
    xy=(real_dup_pct, g4["sddaas"][idx_real]),
    xytext=(35, 62),
    fontsize=7.0, color=SDDaaS.COLOR, ha="center",
    arrowprops=dict(**ARR, color=SDDaaS.COLOR), bbox=BBOX["sd"])
ax4.annotate(
    f"Li 2016 ≈ {g4['li2016'][idx_real]:.2f}%\ncap=18% ★",
    xy=(real_dup_pct, g4["li2016"][idx_real]),
    xytext=(min(real_dup_pct + 20, 74), g4["li2016"][idx_real] - 4),
    fontsize=7.0, color=Li2016.COLOR, ha="center",
    arrowprops=dict(**ARR, color=Li2016.COLOR), bbox=BBOX["li"])

plt.savefig("sddaas_v8.png", dpi=150, bbox_inches="tight",
            facecolor=fig.get_facecolor())
print("[Output] Graph saved → sddaas_v8.png")
plt.show()
plt.close()
print("[Done]")