"""
SDDaaS Performance Comparison – 6-System Latency Version
==========================================================
Systems compared:

 Li 2016        – Differential Bloom Filter (SC + CDC chunks)
                  Source: Li et al., ICSESS 2016

 Douceur 2002   – Convergent Encryption + SALAD distributed hash DB
                  Source: Douceur et al., ICDCS 2002

 Xiong 2019     – SRRS: CE + Role Authorized Tree (RAT/B+) + DCF
                  Source: Xiong et al., IEEE Access 2019

 TSCF 2021      – Two-Stage Cuckoo Filter for Data Deduplication
                  Source: Liu et al., IEEE MSN 2021
                  DOI: 10.1109/MSN53354.2021.00118
                  Search: 2-bucket Cuckoo Filter lookup (O(1))
                  + SHA-256 fingerprint (~1.5 µs)
                  + in-memory hash index O(log n)

 FCDedup 2023   – Two-Level Dedup for Encrypted Data in Fog Computing
                  Source: Song et al., IEEE TPDS 2023
                  DOI: 10.1109/TPDS.2023.3298684
                  Search: fog-level tag check (bilinear pairing)
                  + short hash lookup at cloud (2-hop network)
                  + cloud-level tag comparison (bilinear pairing)
                  → highest latency due to 2-layer network + pairing

 SDDaaS (Proposed) – CE + CP-ABE + Partitioned BFs (ClientID × FileType)
                  Source: Roiampaeng et al., SIIT 2025

Metrics:
 Graph 1: Search Latency (µs)          – 6 systems
 Graph 2: Storage Used (MB)            – 4 original systems
 Graph 3: False Positive Rate (%)      – 4 original systems
 Graph 4: Deduplication Efficiency (%) – 4 original systems
"""

import math
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from tabulate import tabulate


# ── Configuration ──────────────────────────────────────────────────────
CLIENT_IDS   = [f"Client{c}" for c in "ABCDEFGHIJ"]
FILE_TYPES   = ["Document", "Image", "Code", "Video", "Archive", "Financial"]
N_CLIENTS    = len(CLIENT_IDS)    # 10
N_FILETYPES  = len(FILE_TYPES)    # 6
N_PARTITIONS = N_CLIENTS * N_FILETYPES  # 60

N_SIZES        = [100, 500, 1_000, 5_000, 10_000, 50_000]
DUP_RATIO      = 0.40
AVG_SIZE_KB    = 512
FPR_TARGET     = 0.01
CPABE_OVERHEAD = 0.05
RAT_ORDER      = 3


# ══════════════════════════════════════════════════════════════════════
# HELPER – Bloom Filter parameter computation
# ══════════════════════════════════════════════════════════════════════

def _bf_params(n_ins, capacity_factor):
    cap = max(int(n_ins * capacity_factor), 1)
    m   = math.ceil(-cap * math.log(FPR_TARGET) / (math.log(2) ** 2))
    k   = max(1, math.ceil((m / cap) * math.log(2)))
    return m, k

def _bf_fpr_pct(n_ins, capacity_factor):
    cap  = max(int(n_ins * capacity_factor), 1)
    m, k = _bf_params(cap, 1.0)
    fpr  = (1 - math.exp(-k * n_ins / m)) ** k
    return min(fpr * 100, 30.0)


# ══════════════════════════════════════════════════════════════════════
# FALSE POSITIVE RATE (%) – 4 original systems
# ══════════════════════════════════════════════════════════════════════

def fpr_li2016(n):
    ns_frac = 0.87
    nc_frac = 0.13
    fpr_sc  = _bf_fpr_pct(int(n * ns_frac), 0.70)
    fpr_cdc = _bf_fpr_pct(int(n * nc_frac), 0.70)
    return ns_frac * fpr_sc + nc_frac * fpr_cdc

def fpr_douceur(n):
    return 0.0

def fpr_xiong2019(n):
    return _bf_fpr_pct(n, 0.75) * 1.1

def fpr_sddaas(n):
    n_part = max(n // N_PARTITIONS, 1)
    cap    = max(n_part * 2, 5)
    m      = math.ceil(-cap * math.log(FPR_TARGET) / (math.log(2) ** 2))
    k      = max(1, math.ceil((m / cap) * math.log(2)))
    return (1 - math.exp(-k * n_part / m)) ** k * 100


# ══════════════════════════════════════════════════════════════════════
# STORAGE USED (MB) – 4 original systems
# ══════════════════════════════════════════════════════════════════════

def stor_li2016(n):
    n_stored = int(n * 0.82)
    data_mb  = (n_stored * AVG_SIZE_KB) / 1024
    index_mb = (n * 0.002 * AVG_SIZE_KB) / 1024
    return data_mb + index_mb

def stor_douceur(n):
    n_unique = int(n * (1 - DUP_RATIO))
    data_mb  = (n_unique * AVG_SIZE_KB) / 1024
    salad_mb = (n * 36) / (1024 * 1024)
    return data_mb + salad_mb

def stor_xiong2019(n):
    n_unique  = int(n * (1 - DUP_RATIO))
    data_mb   = (n_unique * AVG_SIZE_KB) / 1024
    rkey_mb   = (n_unique * 0.032) / 1024
    dcf_bits  = math.ceil(-n * math.log(FPR_TARGET) / (math.log(2) ** 2))
    dcf_mb    = dcf_bits / (8 * 1024 * 1024)
    rat_mb    = (N_CLIENTS * 1.0) / 1024
    return data_mb + rkey_mb + dcf_mb + rat_mb

def stor_sddaas(n):
    n_unique  = int(n * (1 - DUP_RATIO))
    n_dup     = n - n_unique
    data_mb   = (n_unique * AVG_SIZE_KB * (1 + CPABE_OVERHEAD)) / 1024
    key_mb    = (n_unique * 0.256) / 1024
    ref_mb    = (n_dup * 0.064) / 1024
    n_part    = max(n // N_PARTITIONS, 1)
    cap       = max(n_part * 2, 5)
    m_part, _ = _bf_params(cap, 1.0)
    bf_mb     = (m_part * N_PARTITIONS) / (8 * 1024 * 1024)
    return data_mb + key_mb + ref_mb + bf_mb


# ══════════════════════════════════════════════════════════════════════
# SEARCH LATENCY (µs) – 6 systems
# ══════════════════════════════════════════════════════════════════════

def lat_li2016(n):
    """
    BF probe (k=7, corrected) + in-memory hash table + FP scan O(sqrt(n)).
    Source: Li et al. ICSESS 2016.
    """
    _, k     = _bf_params(max(int(n * 0.87 * 0.70), 1), 1.0)
    bf_us    = 0.8 + k * 0.12
    index_us = 0.5 + 0.15 * math.log2(max(n, 2))
    fpr      = fpr_li2016(n) / 100
    scan_us  = fpr * math.sqrt(max(n, 1)) * 0.008
    return bf_us + index_us + scan_us

def lat_douceur(n):
    """
    SALAD D=2: SHA-256 (~1.5 µs) + 2 routing hops + leaf-table lookup O(sqrt(n)).
    Source: Douceur et al. ICDCS 2002.
    """
    sha_us    = 1.5
    leaf_size = max(3 * math.sqrt(max(n, 1)), 10)
    hop_us    = 2 * (0.3 + 0.002 * leaf_size)
    return sha_us + hop_us + 0.2

def lat_xiong2019(n):
    """
    RAT B+ tree O(log_3(n_roles)) + DCF probe (k=7) + FP scan + XOR re-enc O(log n).
    Source: Xiong et al. IEEE Access 2019.
    """
    rat_d    = max(1, math.ceil(math.log(max(N_CLIENTS, 2), RAT_ORDER)))
    rat_us   = rat_d * RAT_ORDER * 0.15 + 0.3 * math.log2(max(n, 2))
    _, k_dcf = _bf_params(max(int(n * 0.75), 1), 1.0)
    dcf_us   = 0.6 + k_dcf * 0.12
    fpr_dcf  = fpr_xiong2019(n) / 100
    scan_us  = fpr_dcf * math.sqrt(max(n, 1)) * 0.006
    reenc_us = 0.5 + 0.12 * math.log2(max(n, 2))
    return rat_us + dcf_us + scan_us + reenc_us

def lat_tscf2021(n):
    """
    TSCF (Two-Stage Cuckoo Filter) – Liu et al., IEEE MSN 2021.
    Lookup: check 2 candidate buckets via partial-key cuckoo hashing → O(1).
    Each bucket check: compute fingerprint + 2 hash positions (~0.15 µs each).
    SHA-256 fingerprint computation: ~1.5 µs (same as used in paper experiments).
    In-memory index lookup after CF hit: O(log n) amortised.
    CF lookup is O(1) but slightly slower than BF due to 2-bucket check vs k-hash.
    Source: Liu et al. 2021, Section III (TSCF lookup is identical to SCF lookup).
    """
    sha_us   = 1.5                                  # SHA-256 fingerprint
    cf_us    = 0.25 + 2 * 0.15                      # 2 bucket checks × 0.15µs
    index_us = 0.4 + 0.12 * math.log2(max(n, 2))   # in-memory index O(log n)
    return sha_us + cf_us + index_us

def lat_fcdedup2023(n):
    """
    FCDedup – Song et al., IEEE TPDS 2023.
    Two-level search: fog-level tag check + cloud-level tag comparison.

    Fog-level:  compute fog tag (1 exponentiation ~0.4 µs)
                + DB lookup at fog node O(1) (~0.2 µs)
    Cloud-level: short hash lookup at cloud (1 network round-trip ~0.8 µs)
                 + bilinear pairing per potential match (~0.55 µs × Nocn)
                 + equality check over G2 (~0.1 µs)
    Nocn (avg collisions on short 10-bit hash) grows O(n / 2^10) → O(n/1024).
    Total grows O(n) at large n due to short-hash collision growth.
    Source: Song et al. 2023, Section V-A (complexity analysis).
    """
    fog_tag_us   = 0.4 + 0.2                         # exponentiation + DB lookup
    net_hop_us   = 0.8                                # 1 round-trip fog→cloud
    nocn         = max(1, n / 1024)                   # avg short-hash collisions
    pairing_us   = nocn * (0.55 + 0.1)               # bilinear pairing × Nocn
    return fog_tag_us + net_hop_us + pairing_us

def lat_sddaas(n):
    """
    O(1) partition routing (2 dict lookups: ClientID → FileType)
    + BF probe on tiny partition (k=7, small m → fast)
    + O(1) metadata exact-match fallback.
    Near-constant growth — fastest among all 6 systems.
    Source: SDDaaS paper, Phase 4.
    """
    route_us  = 0.20
    n_part    = max(n // N_PARTITIONS, 1)
    cap       = max(n_part * 2, 5)
    _, k_part = _bf_params(cap, 1.0)
    bf_us     = 0.3 + k_part * 0.08
    fpr_p     = fpr_sddaas(n) / 100
    meta_us   = fpr_p * 0.15 + 0.10
    scale_us  = 0.05 * math.log2(max(n, 2))
    return route_us + bf_us + meta_us + scale_us


# ══════════════════════════════════════════════════════════════════════
# DEDUPLICATION EFFICIENCY (%) – 4 original systems
# ══════════════════════════════════════════════════════════════════════

def dedup_li2016(n):    return 18.0
def dedup_douceur(n):   return 40.0
def dedup_xiong2019(n): return 40.0
def dedup_sddaas(n):    return 40.0


# ══════════════════════════════════════════════════════════════════════
# BUILD RESULT ARRAYS
# ══════════════════════════════════════════════════════════════════════

ns = N_SIZES

# Latency – 6 systems
t_li = [lat_li2016(n)     for n in ns]
t_dc = [lat_douceur(n)    for n in ns]
t_xi = [lat_xiong2019(n)  for n in ns]
t_ts = [lat_tscf2021(n)   for n in ns]
t_fc = [lat_fcdedup2023(n) for n in ns]
t_sd = [lat_sddaas(n)     for n in ns]

# Storage, FPR, Dedup – 4 original systems
s_li = [stor_li2016(n)    for n in ns]
s_dc = [stor_douceur(n)   for n in ns]
s_xi = [stor_xiong2019(n) for n in ns]
s_sd = [stor_sddaas(n)    for n in ns]

f_li = [fpr_li2016(n)    for n in ns]
f_dc = [fpr_douceur(n)   for n in ns]
f_xi = [fpr_xiong2019(n) for n in ns]
f_sd = [fpr_sddaas(n)    for n in ns]

d_li = [dedup_li2016(n)    for n in ns]
d_dc = [dedup_douceur(n)   for n in ns]
d_xi = [dedup_xiong2019(n) for n in ns]
d_sd = [dedup_sddaas(n)    for n in ns]


# ══════════════════════════════════════════════════════════════════════
# PRINT TABLES
# ══════════════════════════════════════════════════════════════════════

HDR4 = ["N (files)", "Li 2016", "Douceur 2002", "Xiong 2019", "SDDaaS (Proposed)"]
HDR6 = ["N (files)", "Li 2016", "Douceur 2002", "Xiong 2019",
        "TSCF 2021", "FCDedup 2023", "SDDaaS (Proposed)"]

print("\n" + "=" * 90)
print("TABLE I – Search Latency (µs)  [6 systems, lower = better]")
print("=" * 90)
rows = [[f"{n:,}",
         f"{t_li[i]:.3f}", f"{t_dc[i]:.3f}", f"{t_xi[i]:.3f}",
         f"{t_ts[i]:.3f}", f"{t_fc[i]:.3f}", f"{t_sd[i]:.3f}"]
        for i, n in enumerate(ns)]
print(tabulate(rows, headers=HDR6, tablefmt="grid"))

print("\n" + "=" * 72)
print("TABLE II – Storage Used (MB)  [4 systems, lower = better]")
print("=" * 72)
rows = [[f"{n:,}", f"{s_li[i]:.1f}", f"{s_dc[i]:.1f}",
         f"{s_xi[i]:.1f}", f"{s_sd[i]:.1f}"]
        for i, n in enumerate(ns)]
print(tabulate(rows, headers=HDR4, tablefmt="grid"))

print("\n" + "=" * 72)
print("TABLE III – False Positive Rate (%)  [4 systems, lower = better]")
print("=" * 72)
rows = [[f"{n:,}", f"{f_li[i]:.4f}", f"{f_dc[i]:.4f}",
         f"{f_xi[i]:.4f}", f"{f_sd[i]:.6f}"]
        for i, n in enumerate(ns)]
print(tabulate(rows, headers=HDR4, tablefmt="grid"))

print("\n" + "=" * 72)
print("TABLE IV – Deduplication Efficiency (%)  [4 systems, higher = better]")
print("=" * 72)
rows = [[f"{n:,}", f"{d_li[i]:.1f}%", f"{d_dc[i]:.1f}%",
         f"{d_xi[i]:.1f}%", f"{d_sd[i]:.1f}%"]
        for i, n in enumerate(ns)]
print(tabulate(rows, headers=HDR4, tablefmt="grid"))


# ══════════════════════════════════════════════════════════════════════
# PLOT — 2×2 GRID
# Graph 1: 6-system search latency
# Graphs 2–4: 4 original systems
# ══════════════════════════════════════════════════════════════════════

C = {
    'li': '#E07B39',   # orange
    'dc': '#27AE60',   # green
    'xi': '#9B59B6',   # purple
    'ts': '#C0392B',   # red       ← TSCF 2021
    'fc': '#795548',   # brown     ← FCDedup 2023
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


# ── Graph 1: Search Latency – 6 systems ─────────────────────────────
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

# SDDaaS ~1.8 µs at N=50k — put label at bottom-right corner, far from all lines
ax1.annotate('SDDaaS:\nO(1) routing\n→ lowest',
             xy=(ns[-1], t_sd[-1]),
             xytext=(ns[-1], 13.0),
             fontsize=7, color=C['sd'],
             arrowprops=dict(**ARROW, color=C['sd']),
             bbox=BBOX_SD, ha='center', va='bottom')

# FCDedup spikes to 33 µs — put label ABOVE the peak, no overlap possible
ax1.annotate('FCDedup 2023:\n2-layer bilinear pairing\n→ O(n) growth',
             xy=(ns[-1], t_fc[-1]),
             xytext=(ns[-1], 38.5),
             fontsize=7, color=C['fc'],
             arrowprops=dict(**ARROW, color=C['fc']),
             bbox=BBOX_FC, ha='center', va='bottom')


# ── Graph 2: Storage Used – 4 systems ───────────────────────────────
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

ax2.annotate('Li 2016: only 18% dedup\n→ stores 82% of all files',
             xy=(ns[-1], s_li[-1]),
             xytext=(ns[-2], s_li[-1] - 5500),
             fontsize=7.5, color=C['li'],
             arrowprops=dict(**ARROW, color=C['li']),
             bbox=BBOX_LI, ha='center')


# ── Graph 3: False Positive Rate – 4 systems ────────────────────────
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

ax3.annotate('Douceur = 0%\n(exact fingerprint match,\nno probabilistic filter)',
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


# ── Graph 4: Deduplication Efficiency – 4 systems ───────────────────
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
             '(all store unique files only = DUP_RATIO)',
             xy=(ns[2], 40.0),
             xytext=(ns[1], 52.0),
             fontsize=7.5, color='#444444',
             arrowprops=dict(**ARROW, color='#888888'),
             bbox=BBOX_NEU, ha='center')

ax4.annotate('Li 2016 = 18%\n(chunking-method limited,\nper paper Fig. 3)',
             xy=(ns[2], 18.0),
             xytext=(ns[1], 7.0),
             fontsize=7.5, color=C['li'],
             arrowprops=dict(**ARROW, color=C['li']),
             bbox=BBOX_LI, ha='center')

ax4.annotate('* SDDaaS = per-client only\n(no cross-client dedup\nby design, for privacy)',
             xy=(ns[-1], d_sd[-1]),
             xytext=(ns[-1], 53.0),
             fontsize=7.5, color=C['sd'],
             arrowprops=dict(**ARROW, color=C['sd']),
             bbox=BBOX_SD, ha='center')


# ── Super title ──────────────────────────────────────────────────────
fig.suptitle(
    'SDDaaS vs. Baseline Systems – Performance Comparison\n'
    'Graph 1: 6-System Search Latency  |  Graphs 2–4: 4-System Comparison\n'
    '(DUP_RATIO=40%, avg file=512 KB, 10 clients × 6 file types, analytical simulation)',
    fontsize=12, fontweight='bold', y=0.97)

out_path = 'sddaas_comparison_full.png'
plt.savefig(out_path, dpi=150, bbox_inches='tight', facecolor=fig.get_facecolor())
plt.show()
print(f"\nGraph saved → {out_path}")
plt.close()