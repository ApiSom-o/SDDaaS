import math
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from tabulate import tabulate

# ── Configuration ──────────────────────────────────────────────────────
CLIENT_IDS   = [f"Client{c}" for c in "ABCDEFGHIJ"]
FILE_TYPES   = ["Document", "Image", "Code", "Video", "Archive", "Financial"] 
N_CLIENTS    = len(CLIENT_IDS)    # 10
N_FILETYPES  = len(FILE_TYPES)    # 6
N_PARTITIONS = N_CLIENTS * N_FILETYPES  # 60 partitions = 10 clients × 6 file types

N_SIZES        = [100, 500, 1_000, 5_000, 10_000, 50_000]  # ขนาด n ที่ทดสอบ
DUP_RATIO      = 0.40   # 40% ของไฟล์ซ้ำกัน
AVG_SIZE_KB    = 512    
FPR_TARGET     = 0.01   
CPABE_OVERHEAD = 0.05   # CP-ABE เพิ่ม overhead 5% ต่อไฟล์ (ประมาณจาก CP-ABE literature)
RAT_ORDER      = 3      # B+ tree order ของ RAT ใน Xiong 2019


# ══════════════════════════════════════════════════════════════════════
# HELPER
# ══════════════════════════════════════════════════════════════════════

def _bf_params(n_ins, capacity_factor): # คำนวณ m และ k 
   cap = max(int(n_ins * capacity_factor), 1) 
   m   = math.ceil(-cap * math.log(FPR_TARGET) / (math.log(2) ** 2))
   k   = max(1, math.ceil((m / cap) * math.log(2)))
   return m, k


def _bf_fpr_pct(n_ins, capacity_factor): # คำนวณ FPR เป็นเปอร์เซ็นต์
   cap  = max(int(n_ins * capacity_factor), 1)
   m, k = _bf_params(cap, 1.0)
   fpr  = (1 - math.exp(-k * n_ins / m)) ** k
   return min(fpr * 100, 30.0)


# ══════════════════════════════════════════════════════════════════════
# FALSE POSITIVE RATE
# ══════════════════════════════════════════════════════════════════════

def fpr_li2016(n):# Li 2016: ใช้ 2 BF แยกกัน คือ SC และ CDC chunks 
    # capacity factor = 0.70 = BF ออกแบบมาสำหรับ 70% ของ load จริง เลยทำให้ FPR สูงขึ้นเมื่อ n โต
    ns_frac, nc_frac = 0.87, 0.13 # สัดส่วนของ SC และ CDC chunks ตาม Li 2016 Fig.3
    fpr_sc  = _bf_fpr_pct(int(n * ns_frac), 0.70) # FPR ของ SC 
    fpr_cdc = _bf_fpr_pct(int(n * nc_frac), 0.70) # FPR ของ CDC 
    return ns_frac * fpr_sc + nc_frac * fpr_cdc # เฉลี่ย FPR ตามสัดส่วน

def fpr_douceur(n): # Douceur 2002: ใช้ SALAD DHT แทน BF เลยไม่มี FPR เพราะเป็น exact match
    return 0.0 

def fpr_xiong2019(n): # Xiong 2019: ใช้ DCF แทน BF ปกติ (DCF มี FPR สูงกว่า BF มาตรฐานประมาณ 10%) 
    return _bf_fpr_pct(n, 0.75) * 1.1 # DCF = 75% ของ load เลยFPR สูงขึ้น และเพิ่ม 10% penalty

def fpr_sddaas(n): # SDDaaS: แต่ละ partition มี n/60 items = BF เล็กมาก FPR ต่ำมาก
    n_part = max(n // N_PARTITIONS, 1) # items ต่อ partition (อย่างน้อย 1)
    cap    = max(n_part * 2, 5)  # capacity = 2× items ต่อ partition (safety margin)
    m = math.ceil(-cap * math.log(FPR_TARGET) / (math.log(2) ** 2)) # คำนวณ m 
    k = max(1, math.ceil((m / cap) * math.log(2))) # คำนวณ k 
    fpr = (1 - math.exp(-k * n_part / m)) ** k # คำนวณ FPR 
    return fpr * 100 # แปลงเป็นเปอร์เซ็นต์ (SDDaaS มี FPR ต่ำมากเพราะ partitioned BF มี n_part เล็กมาก)


# ══════════════════════════════════════════════════════════════════════
# STORAGE (MB)
# ══════════════════════════════════════════════════════════════════════

def stor_li2016(n):# Li 2016: dedup efficiency ~18% (จาก Fig.3)
    n_stored = int(n * 0.82) # 82% ของไฟล์ถูกเก็บจริง (bc dedup efficiency ~18%)
    data_mb  = (n_stored * AVG_SIZE_KB) / 1024 # ขนาดไฟล์ที่เก็บใน MB
    index_mb = (n * 0.002 * AVG_SIZE_KB) / 1024 # index overhead 0.2% ของ n × avg file size (from Section 4.2)
    return data_mb + index_mb # รวมขนาดไฟล์และ index overhead

def stor_douceur(n):# Douceur 2002: เก็บเฉพาะ unique files + SALAD metadata 36 bytes ต่อไฟล์ (from Section 4)
    n_unique = int(n * (1 - DUP_RATIO)) # จำนวนไฟล์ที่unique
    data_mb  = (n_unique * AVG_SIZE_KB) / 1024 # ขนาดไฟล์ที่เก็บใน MB (แต่เฉพาะ unique files)
    salad_mb = (n * 36) / (1024 * 1024)  # 36 bytes per entry
    return data_mb + salad_mb # รวมขนาดไฟล์และ SALAD metadata overhead

def stor_xiong2019(n): # Xiong 2019: unique files + re-encryption key 32 bytes + DCF bits + RAT overhead
    # 32 bytes per key: ขนาด XOR re-encryption key
    # RAT: 1 KB per client สำหรับ Role Authorized Tree
    n_unique  = int(n * (1 - DUP_RATIO)) # จำนวนไฟล์ที่unique 
    data_mb   = (n_unique * AVG_SIZE_KB) / 1024 # ขนาดไฟล์ที่เก็บใน MB (เฉพาะ unique files)
    rkey_mb   = (n_unique * 0.032) / 1024          # 32 bytes per re-enc key 
    dcf_bits  = math.ceil(-n * math.log(FPR_TARGET) / (math.log(2) ** 2)) # จำนวน bits ที่ DCF ใช้สูตรของ standard BF theory (แต่Xiongใช้ DCF แทน BF)
    dcf_mb    = dcf_bits / (8 * 1024 * 1024) # แปลง bits เป็น MB
    rat_mb    = (N_CLIENTS * 1.0) / 1024           # 1 KB per client สำหรับ RAT 
    return data_mb + rkey_mb + dcf_mb + rat_mb # รวมขนาดไฟล์จริง, re-encryption keys, DCF bits, และ RAT overhead

def stor_sddaas(n): # SDDaaS: unique files + CP-ABE + CE key + ref pointer(for duplicate) + BF bits
    n_unique  = int(n * (1 - DUP_RATIO)) # จำนวนไฟล์ที่unique
    n_dup     = n - n_unique # จำนวนduplicates files
    data_mb   = (n_unique * AVG_SIZE_KB * (1 + CPABE_OVERHEAD)) / 1024  # CP-ABE overhead 
    key_mb    = (n_unique * 0.256) / 1024    # 256 bytes per CE key 
    ref_mb    = (n_dup * 0.064) / 1024       # 64 bytes per duplicate pointer 
    n_part    = max(n // N_PARTITIONS, 1) # items ต่อ partition (อย่างน้อย 1)
    cap       = max(n_part * 2, 5) # capacity = 2× items ต่อ partition (safety margin)
    m_part    = math.ceil(-cap * math.log(FPR_TARGET) / (math.log(2) ** 2)) # คำนวณ m 
    bf_mb     = (m_part * N_PARTITIONS) / (8 * 1024 * 1024)  # รวม BF bits ทุก partition
    return data_mb + key_mb + ref_mb + bf_mb # รวมallสำหรับทุก partition


# ══════════════════════════════════════════════════════════════════════
# SEARCH LATENCY (µs)– 6 systems
# ══════════════════════════════════════════════════════════════════════

def lat_li2016(n): # เวลารวม = เวลา probe BF + เวลา lookup index + เวลา scan กรณีเจอ false positive
   _, k     = _bf_params(max(int(n * 0.87 * 0.70), 1), 1.0)  # k = จำนวน hash functions ของ BF
   bf_us    = 0.8 + k * 0.12       # BF probe: base 0.8 µs + 0.12 µs ต่อ hash function
   index_us = 0.5 + 0.15 * math.log2(max(n, 2))  # hash table lookup: O(log n) เพราะ index โตตาม n
   fpr      = fpr_li2016(n) / 100
   scan_us  = fpr * math.sqrt(max(n, 1)) * 0.008  # FP scan: ถ้า BF บอก hit แต่จริงๆ miss ต้อง scan O(sqrt(n)) items
   return bf_us + index_us + scan_us


def lat_douceur(n):# SHA-256 hash + 2 routing hops ใน DHT + leaf lookup ไม่มี BF เลยไม่มี FP scan แต่ต้องเสีย latency จาก network routing 2 hops
   sha_us    = 1.5                                 #common benchmark estimate
   leaf_size = max(3 * math.sqrt(max(n, 1)), 10)  # leaf bucket size = 3×sqrt(n) จาก SALAD D=2 routing analysis (Section 3)
   hop_us    = 2 * (0.3 + 0.002 * leaf_size)      # 2 routing hops, แต่ละ hop = 0.3 + 0.002×leaf_size µs
   return sha_us + hop_us + 0.2                   # +0.2 µs สำหรับ leaf table lookup


def lat_xiong2019(n): #RAT B+ tree traversal + DCF probe + FP scan + XOR re-encryption
   rat_d    = max(1, math.ceil(math.log(max(N_CLIENTS, 2), RAT_ORDER)))  # depth ของ RAT = log_3(N_CLIENTS) (Section 4)
   rat_us   = rat_d * RAT_ORDER * 0.15 + 0.3 * math.log2(max(n, 2))     # RAT traversal: 0.15 µs per node + B+ tree O(log n) overhead
   _, k_dcf = _bf_params(max(int(n * 0.75), 1), 1.0)                     # k ของ DCF
   dcf_us   = 0.6 + k_dcf * 0.12          # DCF probe: base 0.6 µs + 0.12 µs per hash (same as BF but use DCF)
   fpr_dcf  = fpr_xiong2019(n) / 100
   scan_us  = fpr_dcf * math.sqrt(max(n, 1)) * 0.006  # FP scan: 0.006 µs per item 
   reenc_us = 0.5 + 0.12 * math.log2(max(n, 2))       # XOR re-encryption overhead: O(log n) ( Section 5)
   return rat_us + dcf_us + scan_us + reenc_us


def lat_tscf2021(n):# Two-Stage Cuckoo Filter: SHA-256 fingerprint + 2-bucket CF lookup + index
   # CF lookup เป็น O(1) เหมือน BF แต่ช้ากว่านิดหน่อยเพราะต้อง check 2 buckets แทน k hash positions
   sha_us   = 1.5                                  # SHA-256 fingerprint ~1.5 µs (Section 3)
   cf_us    = 0.25 + 2 * 0.15                      # CF lookup: base 0.25 µs + 2 bucket checks × 0.15 µs per bucket
   index_us = 0.4 + 0.12 * math.log2(max(n, 2))   # in-memory hash index lookup: O(log n)
   return sha_us + cf_us + index_us


def lat_fcdedup2023(n): # Fog+Cloud: fog-level tag check + network hop + cloud-level bilinear pairing
   # latency สูงที่สุดเพราะต้องทำ bilinear pairing ซึ่งโตแบบ O(n) ตามจำนวน collision ของ short hash
   fog_tag_us   = 0.4 + 0.2                         # fog tag: 0.4 µs exponentiation + 0.2 µs DB lookup (Section V.A-complexity)
   net_hop_us   = 0.8                                # 1 round-trip fog→cloud ~0.8 µs
   nocn         = max(1, n / 1024)                   # avg collision ของ short 10-bit (V.A-complexity)
   pairing_us   = nocn * (0.55 + 0.1)               # bilinear pairing × Nocn: 0.55 µs per pairing + 0.1 µs equality check
   return fog_tag_us + net_hop_us + pairing_us       # O(n) growth เพราะ nocn โตตาม n


def lat_sddaas(n):
   # เร็วที่สุดเพราะ partition ทำให้ BF เล็กมาก + routing เป็น O(1) ไม่มี tree traversal หรือ network hop
   route_us  = 0.20                                  # O(1) routing: 2 dict lookups × 0.10 µs 
   n_part    = max(n // N_PARTITIONS, 1)             # items ต่อ partition (n/60) → BF เล็กมาก k น้อย probe เร็ว
   cap       = max(n_part * 2, 5)
   _, k_part = _bf_params(cap, 1.0)
   bf_us     = 0.3 + k_part * 0.08                  # BF probe บน partition เล็ก: 0.08 µs per hash (เร็วกว่า 2016/2019 เพราะ partition เล็ก)
   fpr_p     = fpr_sddaas(n) / 100
   meta_us   = fpr_p * 0.15 + 0.10                  # metadata exact match: base 0.10 µs + FP penalty (แทบเป็น 0 เพราะ FPR ต่ำมาก)
   scale_us  = 0.05 * math.log2(max(n, 2))          # slight growth จาก metadata dict size (เกือบ flat)
   return route_us + bf_us + meta_us + scale_us


# ══════════════════════════════════════════════════════════════════════
# DEDUPLICATION EFFICIENCY
# ══════════════════════════════════════════════════════════════════════

def dedup_eff_li2016(n):
    base    = 0.18  # 18% มาจาก Fig.3 
    penalty = fpr_li2016(n) / 100 * 0.05  # FP penalty เล็กน้อยเมื่อ n โต
    return max(base - penalty, 0.10) # หัก penalty จาก FPR ที่ทำให้ dedup ผิด แต่ประสิทธิภาพขั้นต่ำ 10%

def dedup_eff_douceur(n):
    raw_reclaim    = 0.46   # raw_reclaim = 46% มาจาก Section 5
    lambda_rep     = 2.5    # lambda_rep = 2.5 มาจาก SALAD fault-tolerance replication factor  sec4.2
    return raw_reclaim / lambda_rep # SALAD ทำ dedup เฉพาะ unique files ไม่มี FP เลยประสิทธิภาพคงที่ที่ 46% ÷ 2.5 = 18.4%

def dedup_eff_xiong2019(n):
    penalty = fpr_xiong2019(n) / 100 * 0.03 # FP penalty เมื่อ n โต เพราะ DCF มี FPR สูงกว่า BF ปกติประมาณ 10% และมีผลต่อ dedup effi
    return max(DUP_RATIO - penalty, 0.30)   # DUP_RATIO = 40% baseline, หัก penalty จาก FP ที่ทำให้ dedup ผิดพลาด

def dedup_eff_sddaas(n): # SDDaaS ทำ dedup เฉพาะภายใน client เดียวกัน ไม่ข้าม client  FP penalty น้อยมากเพราะ partitioned BF มี FPR ต่ำ
    penalty = fpr_sddaas(n) / 100 * 0.01
    return max(DUP_RATIO - penalty, 0.35)


# ══════════════════════════════════════════════════════════════════════
# BUILD ARRAYS
# ══════════════════════════════════════════════════════════════════════

ns = N_SIZES # ขนาด n ที่ทดสอบ

# Latency – 6 systems
t_li = [lat_li2016(n)     for n in ns] 
t_dc = [lat_douceur(n)    for n in ns] 
t_xi = [lat_xiong2019(n)  for n in ns]
t_ts = [lat_tscf2021(n)   for n in ns]
t_fc = [lat_fcdedup2023(n) for n in ns]
t_sd = [lat_sddaas(n)     for n in ns]

# Storage – 4 systems
s_li = [stor_li2016(n)    for n in ns]
s_dc = [stor_douceur(n)   for n in ns]
s_xi = [stor_xiong2019(n) for n in ns]
s_sd = [stor_sddaas(n)    for n in ns]

#False Positive Rate (FPR) – 4 systems
f_li = [fpr_li2016(n)    for n in ns]
f_dc = [fpr_douceur(n)   for n in ns]
f_xi = [fpr_xiong2019(n) for n in ns]
f_sd = [fpr_sddaas(n)    for n in ns]

#Deduplication Efficiency – 4 systems
d_li = [dedup_eff_li2016(n)    * 100 for n in ns]
d_dc = [dedup_eff_douceur(n)   * 100 for n in ns]
d_xi = [dedup_eff_xiong2019(n) * 100 for n in ns]
d_sd = [dedup_eff_sddaas(n)    * 100 for n in ns]


# ══════════════════════════════════════════════════════════════════════
# PRINT TABLES
# ══════════════════════════════════════════════════════════════════════

HDR4 = ["N (files)", "Li 2016", "Douceur 2002", "Xiong 2019", "SDDaaS (Proposed)"]
HDR6 = ["N (files)", "Li 2016", "Douceur 2002", "Xiong 2019", "TSCF 2021", "FCDedup 2023", "SDDaaS (Proposed)"]

print("\n" + "=" * 90)
print("TABLE I – Search Latency (µs)  [6 systems, lower = better]")
print("=" * 90)
print(tabulate(
    [[f"{n:,}",
      f"{t_li[i]:.3f}", f"{t_dc[i]:.3f}", f"{t_xi[i]:.3f}",
      f"{t_ts[i]:.3f}", f"{t_fc[i]:.3f}", f"{t_sd[i]:.3f}"]
     for i, n in enumerate(ns)],
    headers=HDR6, tablefmt="grid", colalign=("right","right","right","right","right","right","right")))
print("\n" + "=" * 72)
print("TABLE II – Storage Used (MB)  [4 systems, lower = better]")
print("=" * 72)
print(tabulate(
    [[f"{n:,}", f"{s_li[i]:.1f}", f"{s_dc[i]:.1f}",
      f"{s_xi[i]:.1f}", f"{s_sd[i]:.1f}"]
     for i, n in enumerate(ns)],
    headers=HDR4, tablefmt="grid", colalign=("right","right","right","right","right")))

print("\n" + "=" * 72)
print("TABLE III – False Positive Rate (%)  [4 systems, lower = better]")
print("=" * 72)
print(tabulate(
    [[f"{n:,}", f"{f_li[i]:.4f}", f"{f_dc[i]:.4f}",
      f"{f_xi[i]:.4f}", f"{f_sd[i]:.6f}"]
     for i, n in enumerate(ns)],
    headers=HDR4, tablefmt="grid", colalign=("right","right","right","right","right")))

print("\n" + "=" * 72)
print("TABLE IV – Deduplication Efficiency (%)  [4 systems, higher = better]")
print("=" * 72)
print(tabulate(
    [[f"{n:,}", f"{d_li[i]:.2f}%", f"{d_dc[i]:.2f}%",
      f"{d_xi[i]:.2f}%", f"{d_sd[i]:.2f}%"]
     for i, n in enumerate(ns)],
    headers=HDR4, tablefmt="grid", colalign=("right","right","right","right","right")))


# ══════════════════════════════════════════════════════════════════════
# PLOT  (2×2 grid)
# ══════════════════════════════════════════════════════════════════════

C = {
   'li': '#E07B39',   # orange
   'dc': '#27AE60',   # green
   'xi': '#9B59B6',   # purple
   'ts': '#C0392B',   # red      
   'fc': '#795548',   # brown     
   'sd': '#2980B9',   # blue
}
M  = {'li': 'o', 'dc': '^', 'xi': 's', 'ts': 'P', 'fc': 'X', 'sd': 'D'}
LW = {'li': 1.6, 'dc': 1.6, 'xi': 1.6, 'ts': 1.6, 'fc': 1.6, 'sd': 2.8}
LABELS = {
   'li': 'Li 2016 (Diff-BF)',
   'dc': 'Douceur 2002 (SALAD)',
   'xi': 'Xiong 2019 (SRRS)',
   'ts': 'TSCF 2021 (Two-Stage CF)',
   'fc': 'FCDedup 2023 (Fog+Cloud)',
   'sd': 'SDDaaS (Proposed)',
}


BBOX_SD  = dict(boxstyle='round,pad=0.35', fc='#EBF5FB', ec=C['sd'],  alpha=0.92)
BBOX_LI  = dict(boxstyle='round,pad=0.35', fc='#FEF5EC', ec=C['li'],  alpha=0.92)
BBOX_DC  = dict(boxstyle='round,pad=0.35', fc='#E8F8EE', ec=C['dc'],  alpha=0.92)
BBOX_FC  = dict(boxstyle='round,pad=0.35', fc='#EFEBE9', ec=C['fc'],  alpha=0.92)
BBOX_NEU = dict(boxstyle='round,pad=0.35', fc='#F9F9F9', ec='#AAAAAA',alpha=0.92)
BBOX_YEL = dict(boxstyle='round,pad=0.35', fc='#FFFBE6', ec='#CCAA00',alpha=0.92)


ARROW = dict(arrowstyle='->', lw=0.8)


def style_ax(ax, title, ylabel=""):
   ax.set_facecolor('#FFFFFF')
   ax.set_title(title, fontsize=11, fontweight='bold', pad=10)
   ax.set_xlabel('N (files)', fontsize=10)
   ax.set_ylabel(ylabel, fontsize=10)
   ax.grid(True, alpha=0.28, linestyle='--')
   ax.spines['top'].set_visible(False)
   ax.spines['right'].set_visible(False)




fig = plt.figure(figsize=(22, 18))
fig.patch.set_facecolor('#F8F9FA')
gs = gridspec.GridSpec(2, 2, figure=fig,
                      wspace=0.42, hspace=0.65,
                      left=0.07, right=0.97,
                      top=0.84, bottom=0.07)


ax1 = fig.add_subplot(gs[0, 0])
ax2 = fig.add_subplot(gs[0, 1])
ax3 = fig.add_subplot(gs[1, 0])
ax4 = fig.add_subplot(gs[1, 1])


# ── Graph 1: Search Latency ──────────────────────────────────────────
for key, vals in [('li', t_li), ('dc', t_dc), ('xi', t_xi),
                 ('ts', t_ts), ('fc', t_fc), ('sd', t_sd)]:
   ax1.plot(ns, vals, marker=M[key], color=C[key],
            linewidth=LW[key], markersize=5.5, label=LABELS[key])


ax1.set_xscale('log')
ax1.set_ylim(-1, 35)
style_ax(ax1, 'Graph 1 – Search Latency (µs) [6 Systems]',
        ylabel='Latency (µs)')


ax1.legend(fontsize=7.5, loc='upper left', framealpha=0.95,
          bbox_to_anchor=(0.01, 0.99))


# SDDaaS 
ax1.annotate('O(1) routing\n→ lowest',
            xy=(ns[-1], t_sd[-1]),
            xytext=(ns[-1], 13.0),
            fontsize=7, color=C['sd'],
            arrowprops=dict(**ARROW, color=C['sd']),
            bbox=BBOX_SD, ha='center', va='bottom')


# FCDedup 
ax1.annotate('2-layer bilinear pairing\n→ O(n) growth',
            xy=(ns[-1], t_fc[-1]),
            xytext=(ns[-1], 38.5),
            fontsize=7, color=C['fc'],
            arrowprops=dict(**ARROW, color=C['fc']),
            bbox=BBOX_FC, ha='center', va='bottom')

# ── Graph 2: Storage Used ────────────────────────────────────────────
for key, vals in [('li', s_li), ('dc', s_dc), ('xi', s_xi), ('sd', s_sd)]:
   ax2.plot(ns, vals, marker=M[key], color=C[key],
            linewidth=LW[key], markersize=5.5, label=LABELS[key])


ax2.set_xscale('log')
style_ax(ax2, 'Graph 2 – Storage Used (MB)', ylabel='Storage (MB)')
ax2.legend(fontsize=8, loc='upper left')


ax2.annotate('Douceur & Xiong overlap\n(both store unique files only\n≈ 60% of total)',
            xy=(ns[3], s_dc[3]),
            xytext=(ns[2], s_dc[3] + 3500),
            fontsize=7.5, color='#555555',
            arrowprops=dict(**ARROW, color='#555555'),
            bbox=BBOX_YEL, ha='center')


ax2.annotate('only 18% dedup\n→ stores 82% of all files',
            xy=(ns[-1], s_li[-1]),
            xytext=(ns[-2], s_li[-1] - 5500),
            fontsize=7.5, color=C['li'],
            arrowprops=dict(**ARROW, color=C['li']),
            bbox=BBOX_LI, ha='center')


# ── Graph 3: False Positive Rate ─────────────────────────────────────
for key, vals in [('li', f_li), ('xi', f_xi), ('sd', f_sd)]:
   ax3.plot(ns, vals, marker=M[key], color=C[key],
            linewidth=LW[key], markersize=5.5, label=LABELS[key])
ax3.plot(ns, f_dc, marker=M['dc'], color=C['dc'],
        linewidth=LW['dc'], markersize=5.5,
        linestyle='--', label=LABELS['dc'])


ax3.set_xscale('log')
ax3.set_ylim(bottom=-0.5, top=7.0)
style_ax(ax3, 'Graph 3 – False Positive Rate (%)', ylabel='FPR (%)')
ax3.legend(fontsize=8, loc='upper right')


ax3.annotate('Douceur = 0%\n(exact fingerprint match)',
            xy=(ns[2], 0.0),
            xytext=(ns[2], 2.2),
            fontsize=7.5, color=C['dc'],
            arrowprops=dict(**ARROW, color=C['dc']),
            bbox=BBOX_DC, ha='center')


ax3.annotate('SDDaaS ≈ 0.025%\n(partitioned BFs,\n60 small filters)',
            xy=(ns[-1], f_sd[-1]),
            xytext=(ns[-2], f_sd[-1] + 1.2),
            fontsize=7.5, color=C['sd'],
            arrowprops=dict(**ARROW, color=C['sd']),
            bbox=BBOX_SD, ha='center')


# ── Graph 4: Deduplication Efficiency ───────────────────────────────
for key, vals in [('li', d_li), ('dc', d_dc), ('xi', d_xi), ('sd', d_sd)]:
   ax4.plot(ns, vals, marker=M[key], color=C[key],
            linewidth=LW[key], markersize=5.5, label=LABELS[key])


ax4.set_xscale('log')
ax4.set_ylim(0, 60)
ax4.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f'{x:.0f}%'))
style_ax(ax4, 'Graph 4 – Deduplication Efficiency (%)',
        ylabel='Efficiency (%)')
ax4.legend(fontsize=8, loc='center right')


ax4.axhline(y=DUP_RATIO * 100, color='gray',
           linestyle=':', linewidth=1.2, alpha=0.5)
ax4.text(ns[0] * 1.15, DUP_RATIO * 100 + 1.2,
        f'Simulation DUP_RATIO = {DUP_RATIO*100:.0f}%',
        fontsize=7.5, color='gray')


ax4.annotate('Douceur, Xiong & SDDaaS overlap at 40%\n'
            '(all store unique files only)',
            xy=(ns[2], 40.0),
            xytext=(ns[1], 52.0),
            fontsize=7.5, color='#444444',
            arrowprops=dict(**ARROW, color='#888888'),
            bbox=BBOX_NEU, ha='center')


ax4.annotate('Li 2016 = 18%',
            xy=(ns[2], 18.0),
            xytext=(ns[1], 7.0),
            fontsize=7.5, color=C['li'],
            arrowprops=dict(**ARROW, color=C['li']),
            bbox=BBOX_LI, ha='center')


ax4.annotate('SDDaaS = per-client only\n(no cross-client dedup)',
            xy=(ns[-1], d_sd[-1]),
            xytext=(ns[-1], 53.0),
            fontsize=7.5, color=C['sd'],
            arrowprops=dict(**ARROW, color=C['sd']),
            bbox=BBOX_SD, ha='center')

# ── Super title ──────────────────────────────────────────────────────
fig.suptitle(
   'SDDaaS vs Related Works – Performance Comparison\n'
   '(DUP_RATIO=40%, avg file=512 KB, 10 clients × 6 file types, analytical simulation)',
   fontsize=12, fontweight='bold', y=0.97)


out_path = 'sddaas_comparison_full.png'
plt.savefig(out_path, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.show()
print(f"\nGraph saved → {out_path}")
plt.close()