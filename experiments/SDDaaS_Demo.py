import math, hashlib, json, threading, webbrowser, time, csv, os, random, struct
from http.server import HTTPServer, BaseHTTPRequestHandler
from collections import Counter

# ─────────────────────────────────────────────────────────────────
# Real pre-seeded files (3 actual SHA-256 hashes)
# ─────────────────────────────────────────────────────────────────
SEEDED_FILES = [
   {
       "filename": "CloudCS.pdf",
       "hash":     "9f4d76176bb4c1a24503d223b2a31980bbb5df02ae12a25ca2b06693da55e9cc",
       "size":     43003354,
       "type":     "Document",
   },
   {
       "filename": "CybersecCS.pdf",
       "hash":     "e6e575b39e5f3d59d7bc2473cb96734f726ad31d777b3f998de5d9321f1f488b",
       "size":     40403384,
       "type":     "Document",
   },
   {
       "filename": "Ch15-ISO27001-2022_Lecture_DrSomchart.pdf",
       "hash":     "23ade9cd056c61f77358992508973c862334e5df7273cfd02b9139ca95a464c3",
       "size":     6133003,
       "type":     "Document",
   },
]

SEEDED_BYTES    = sum(f["size"] for f in SEEDED_FILES)
N_RANDOM_HASHES = 5_000   # random hashes per client (pre-seed scale demo)
TOTAL_PER_CLIENT = len(SEEDED_FILES) + N_RANDOM_HASHES   # 5,003

# File types used for random hash assignment
FILE_TYPES = ["Document", "Image", "Code", "Video", "Archive", "Financial"]


# ─────────────────────────────────────────────────────────────────
# Generate 5,000 deterministic-random SHA-256-like hashes once
# ─────────────────────────────────────────────────────────────────
def _generate_random_hashes(n: int, seed: int = 42) -> list[dict]:
   """Return n file dicts with random 64-hex hashes, sizes, types."""
   rng = random.Random(seed)
   hashes = []
   type_weights = [40, 20, 15, 10, 8, 7]   # Document-heavy, realistic
   for i in range(n):
       raw = struct.pack(">QQ", rng.getrandbits(64), rng.getrandbits(64))
       h   = hashlib.sha256(raw).hexdigest()
       ft  = rng.choices(FILE_TYPES, weights=type_weights, k=1)[0]
       sz  = rng.randint(4096, 500 * 1024 * 1024)   # 4 KB – 500 MB
       hashes.append({"hash": h, "filename": f"rand_file_{i+1:05d}", "size": sz, "type": ft})
   return hashes

RANDOM_HASHES: list[dict] = _generate_random_hashes(N_RANDOM_HASHES)


# ─────────────────────────────────────────────────────────────────
# Bloom Filter
# ─────────────────────────────────────────────────────────────────
class BloomFilter:
   def __init__(self, capacity=TOTAL_PER_CLIENT, fpr=0.01):
       capacity   = max(capacity, 1)
       self.m     = math.ceil(-capacity * math.log(fpr) / (math.log(2) ** 2))
       self.k     = max(1, math.ceil((self.m / capacity) * math.log(2)))
       self.bits  = bytearray((self.m + 7) // 8)  # packed bit array
       self.count = 0

   def _hashes(self, item: str):
       h1 = int(hashlib.md5(item.encode()).hexdigest(),  16)
       h2 = int(hashlib.sha1(item.encode()).hexdigest(), 16)
       return [(h1 + i * h2) % self.m for i in range(self.k)]

   def insert(self, item: str):
       for idx in self._hashes(item):
           self.bits[idx >> 3] |= 1 << (idx & 7)
       self.count += 1

   def query(self, item: str):
       idxs = self._hashes(item)
       vals = [(self.bits[i >> 3] >> (i & 7)) & 1 for i in idxs]
       return all(vals), vals

   def stats(self):
       bits_set = sum(bin(b).count("1") for b in self.bits)
       return {"m": self.m, "k": self.k,
               "count": self.count, "bits_set": bits_set}

   def clone(self) -> "BloomFilter":
       """Return a shallow-copy clone (used for dept-template fast-clone seeding)."""
       bf2       = BloomFilter.__new__(BloomFilter)
       bf2.m     = self.m
       bf2.k     = self.k
       bf2.bits  = bytearray(self.bits)
       bf2.count = self.count
       return bf2


# ─────────────────────────────────────────────────────────────────
# SDDaaS 
# ─────────────────────────────────────────────────────────────────
class SDDaaS:
   def __init__(self):
       self.tree          = {}   # { emp_id: { file_type: BloomFilter } }
       self.metadata      = {}   # { (emp_id, file_type, hash): True }
       self.stored        = []   # physically unique file records
       self.seeded_bytes  = 0
       self.n_employees   = 0
       self.departments   = []
       self.seed_time_ms  = 0
       self.dept_emp_map  = {}
       self.emp_dept_map  = {}
       # v7 extras
       self.n_rand_hashes = 0    # total random hash entries across all clients
       self.sample_hashes = []   # first 10 random hashes for UI display

   # ── internal helpers ─────────────────────────────────────────
   def _get_or_create_bf(self, emp_id: str, file_type: str,
                         capacity: int = TOTAL_PER_CLIENT) -> BloomFilter:
       self.tree.setdefault(emp_id, {})
       if file_type not in self.tree[emp_id]:
           self.tree[emp_id][file_type] = BloomFilter(capacity=capacity, fpr=0.01)
       return self.tree[emp_id][file_type]

   # ── seed ─────────────────────────────────────────────────────
   def seed(self, csv_path: str):
       if not os.path.exists(csv_path):
           print(f"  [WARN] {csv_path} not found — skipping pre-seed")
           return

       with open(csv_path, newline="") as f:
           rows = list(csv.reader(f))[1:]

       dept_counts       = Counter(r[1] for r in rows)
       self.departments  = sorted(dept_counts.keys())
       self.n_employees  = len(rows)
       self.dept_emp_map = {}
       self.emp_dept_map = {}
       for emp, dept in rows:
           self.dept_emp_map.setdefault(dept, []).append(emp)
           self.emp_dept_map[emp] = dept

       t0 = time.perf_counter()

       # ── Build one TEMPLATE BF per file_type ─────────
       # (shared across all depts; all employees get same random seed)
       template_bfs: dict[str, BloomFilter] = {}
       for ft in FILE_TYPES:
           tbf = BloomFilter(capacity=TOTAL_PER_CLIENT, fpr=0.01)
           # insert 3 real hashes that belong to this type
           for sf in SEEDED_FILES:
               if sf["type"] == ft:
                   tbf.insert(sf["hash"])
           # insert all 5,000 random hashes that belong to this type
           for rh in RANDOM_HASHES:
               if rh["type"] == ft:
                   tbf.insert(rh["hash"])
           template_bfs[ft] = tbf

       # ──Per employee — clone template BFs + build metadata ──
       # Random hashes live in BF only (scale demo) 
       # Real files get metadata for exact-match verification
       for emp, dept in rows:
           # Clone each type's template BF for this employee (fast bytearray copy)
           for ft, tbf in template_bfs.items():
               self.tree.setdefault(emp, {})[ft] = tbf.clone()

           # Metadata: 3 real files only (used for duplicate verification on upload)
           for sf in SEEDED_FILES:
               self.metadata[(emp, sf["type"], sf["hash"])] = True

       # ── Physical storage (1 copy per unique file) ───
       for sf in SEEDED_FILES:
           self.stored.append({
               "hash": sf["hash"], "filename": sf["filename"],
               "size": sf["size"], "emp": "ALL", "dept": "ALL",
               "type": sf["type"], "seeded": True,
           })

       self.seeded_bytes  = SEEDED_BYTES
       self.n_rand_hashes = self.n_employees * N_RANDOM_HASHES
       self.sample_hashes = RANDOM_HASHES[:10]   # for UI display

       self.seed_time_ms  = round((time.perf_counter() - t0) * 1000, 1)

       total_entries = self.n_employees * TOTAL_PER_CLIENT
       print(f"  [OK] v7 seed done: "
             f"{self.n_employees:,} employees × {TOTAL_PER_CLIENT:,} files = "
             f"{total_entries:,} BF entries — {self.seed_time_ms} ms")

   # ── main pipeline ────────────────────────────────────────────
   def process_file(self, file_hash, filename, size, emp_id, file_type, dept=None):
       dept_display = dept or self.emp_dept_map.get(emp_id, "?")
       steps   = []
       t_start = time.perf_counter()

       # Routing
       steps.append({
           "step":   1,
           "title":  "Search Tree Routing",
           "detail": f"Root  ->  {dept_display}  ->  {emp_id}  ->  {file_type}",
           "note":   (f"O(1) 3-level routing: confined to partition "
                      f"'{emp_id}/{file_type}' — "
                      f"no full scan across {self.n_employees:,} clients."),
           "status": "info",
       })

       # Bloom Filter
       bf               = self._get_or_create_bf(emp_id, file_type)
       t_bf0            = time.perf_counter()
       bf_hit, bit_vals = bf.query(file_hash)
       t_bf_us          = (time.perf_counter() - t_bf0) * 1e6
       bfstats          = bf.stats()

       steps.append({
           "step":       2,
           "title":      "Bloom Filter — Probabilistic Check",
           "detail":     (f"Partition: {emp_id}/{file_type}  |  "
                          f"k={bfstats['k']} hash functions  |  "
                          f"m={bfstats['m']:,} bits  |  "
                          f"items: {bfstats['count']:,}  |  "
                          f"BF query: {t_bf_us:.3f} µs"),
           "bits":       bit_vals[:8],
           "bf_all_one": bf_hit,
           "note":       ("All bits = 1  ->  'Probable Duplicate' — "
                          "verifying with Metadata Index."
                          if bf_hit else
                          "Found a 0-bit  ->  file is definitely new "
                          "(no false negatives in Bloom Filters)."),
           "status": "warn" if bf_hit else "new",
       })

       if not bf_hit:
           bf.insert(file_hash)
           self.metadata[(emp_id, file_type, file_hash)] = True
           self.stored.append({
               "hash": file_hash, "filename": filename,
               "size": size, "emp": emp_id, "dept": dept_display,
               "type": file_type, "seeded": False,
           })
           t_total = (time.perf_counter() - t_start) * 1e6
           steps.append({
               "step":   3,
               "title":  "File Stored",
               "detail": f"SHA-256: {file_hash}",
               "note":   "Inserted into this employee's Bloom Filter and Metadata Index. "
                         "Ciphertext saved to Cloud Storage.",
               "status": "new",
           })
           return {"duplicate": False, "steps": steps, "hash": file_hash,
                   "total_us": round(t_total, 3), "bf_us": round(t_bf_us, 3)}

       # Metadata exact match
       t_m0        = time.perf_counter()
       key         = (emp_id, file_type, file_hash)
       exact_match = key in self.metadata
       t_meta_us   = (time.perf_counter() - t_m0) * 1e6
       t_total     = (time.perf_counter() - t_start) * 1e6

       steps.append({
           "step":   3,
           "title":  "Metadata Index — Exact Match Verification",
           "detail": (f"Lookup ({emp_id}, {file_type}, "
                      f"{file_hash[:16]}...)  |  "
                      f"Metadata lookup: {t_meta_us:.3f} µs"),
           "note":   ("Exact match found  ->  confirmed duplicate. "
                      "File NOT stored again — 64-byte reference only."
                      if exact_match else
                      "No match  ->  Bloom Filter false positive eliminated. "
                      "Treating as new file."),
           "status": "dup" if exact_match else "new",
       })

       if not exact_match:
           bf.insert(file_hash)
           self.metadata[key] = True
           self.stored.append({
               "hash": file_hash, "filename": filename,
               "size": size, "emp": emp_id, "dept": dept_display,
               "type": file_type, "seeded": False,
           })

       return {
           "duplicate":  exact_match,
           "steps":      steps,
           "hash":       file_hash,
           "total_us":   round(t_total, 3),
           "bf_us":      round(t_bf_us, 3),
           "meta_us":    round(t_meta_us, 3),
       }

   def get_store(self):
       """คืน 15 รายการล่าสุดในที่เก็บ (uploads ก่อน, seeded ตามหลัง) """
       uploads = [r for r in reversed(self.stored) if not r.get("seeded")]
       seeded  = [r for r in self.stored if r.get("seeded")]
       return (uploads + seeded)[:15]

   def storage_summary(self):
       """คืน dict สรุปสถานะ storage """
       new_bytes = sum(r["size"] for r in self.stored if not r.get("seeded"))
       total_entries = self.n_employees * TOTAL_PER_CLIENT
       total_bf_items = sum(
           bf.count
           for emp_bfs in self.tree.values()
           for bf in emp_bfs.values()
       )
       return {
           "stored_unique":  len(self.stored),
           "used_bytes":     self.seeded_bytes + new_bytes,
           "total_entries":  total_entries,
           "n_rand_hashes":  self.n_rand_hashes,
           "sample_hashes":  self.sample_hashes,
           "total_bf_items": total_bf_items,
       }

   def reset(self):
       self.tree.clear()
       self.metadata.clear()
       self.stored.clear()
       self.seeded_bytes  = 0
       self.n_rand_hashes = 0
       self.sample_hashes = []
       self.dept_emp_map  = {}
       self.emp_dept_map  = {}
       self.n_employees   = 0
       self.departments   = []
       self.seed_time_ms  = 0


# ─────────────────────────────────────────────────────────────────
# HTML
# ─────────────────────────────────────────────────────────────────
HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SDDaaS Demo v7 — 5,000 Random Hashes/Client</title>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
  <style>
    /* ── Variables ── */
    :root {
      --bg:      #0a0c10;
      --panel:   #111418;
      --border:  #1e2228;
      --border2: #2a2f38;
      --accent:  #00e5a0;
      --accent2: #0099ff;
      --warn:    #ffb800;
      --danger:  #ff4d4d;
      --purple:  #b57bff;
      --text:    #e8eaf0;
      --muted:   #6b7280;
      --code:    #a8c4ff;
    }

    /* ── Reset ── */
    * { box-sizing: border-box; margin: 0; padding: 0; }

    /* ── Base ── */
    body {
      background:  var(--bg);
      color:       var(--text);
      font-family: 'IBM Plex Sans', sans-serif;
      min-height:  100vh;
      padding:     2rem;
    }

    /* ── Layout ── */
    .layout {
      display:               grid;
      grid-template-columns: 1fr 400px;
      gap:                   1.5rem;
      max-width:             1280px;
      margin:                0 auto;
    }
    @media (max-width: 960px) { .layout { grid-template-columns: 1fr; } }

    /* ── Header ── */
    .header {
      max-width:   1280px;
      margin:      0 auto 1.25rem;
      display:     flex;
      align-items: center;
      gap:         1rem;
    }
    .logo {
      width:           40px;
      height:          40px;
      background:      var(--accent);
      border-radius:   8px;
      display:         flex;
      align-items:     center;
      justify-content: center;
      font-family:     'IBM Plex Mono', monospace;
      font-weight:     500;
      color:           #000;
      font-size:       13px;
      flex-shrink:     0;
    }
    .header-title h1 { font-size: 18px; font-weight: 600; }
    .header-title p  { font-size: 12px; color: var(--muted); margin-top: 2px; }

    /* ── Scale Banner ── */
    .scale-banner {
      max-width:     1280px;
      margin:        0 auto 1.25rem;
      background:    linear-gradient(135deg, rgba(0,229,160,.07), rgba(0,153,255,.07));
      border:        1px solid rgba(0,229,160,.18);
      border-radius: 12px;
      padding:       .875rem 1.5rem;
      display:       flex;
      align-items:   center;
      gap:           1.5rem;
      flex-wrap:     wrap;
    }
    .sc            { text-align: center; min-width: 80px; }
    .sc-num        { font-family: 'IBM Plex Mono', monospace; font-size: 20px; font-weight: 600; color: var(--accent); }
    .sc-num.purple { color: var(--purple); }
    .sc-lbl        { font-size: 10px; color: var(--muted); margin-top: 2px; letter-spacing: .5px; text-transform: uppercase; }
    .sc-div        { width: 1px; height: 36px; background: var(--border2); }

    /* ── Seed Bar ── */
    .seed-bar {
      max-width:     1280px;
      margin:        0 auto 1.25rem;
      background:    rgba(0,229,160,.04);
      border:        1px solid rgba(0,229,160,.22);
      border-radius: 10px;
      padding:       .75rem 1.25rem;
    }
    .seed-bar-title {
      font-size:      11px;
      font-weight:    500;
      letter-spacing: 1px;
      text-transform: uppercase;
      color:          var(--accent);
      margin-bottom:  .6rem;
    }
    .seed-files { display: flex; gap: 8px; flex-wrap: wrap; }
    .seed-file  {
      background:    rgba(0,229,160,.08);
      border:        1px solid rgba(0,229,160,.2);
      border-radius: 6px;
      padding:       6px 10px;
      font-size:     12px;
      max-width:     360px;
    }
    .seed-name { color: var(--accent); font-weight: 500; }
    .seed-size { color: var(--muted); font-size: 11px; margin-top: 1px; font-family: 'IBM Plex Mono', monospace; }
    .seed-hash { color: var(--code); font-family: 'IBM Plex Mono', monospace; font-size: 10px; margin-top: 2px; word-break: break-all; }

    /* ── Random Bar ── */
    .rand-bar {
      max-width:     1280px;
      margin:        0 auto 1.25rem;
      background:    rgba(181,123,255,.04);
      border:        1px solid rgba(181,123,255,.22);
      border-radius: 10px;
      padding:       .75rem 1.25rem;
    }
    .rand-bar-title {
      font-size:      11px;
      font-weight:    500;
      letter-spacing: 1px;
      text-transform: uppercase;
      color:          var(--purple);
      margin-bottom:  .6rem;
      display:        flex;
      align-items:    center;
      gap:            10px;
    }
    .rand-count-pill {
      background:    rgba(181,123,255,.15);
      color:         var(--purple);
      border-radius: 20px;
      padding:       2px 10px;
      font-size:     11px;
      font-family:   'IBM Plex Mono', monospace;
    }
    .rand-files { display: flex; gap: 6px; flex-wrap: wrap; }
    .rand-file  {
      background:    rgba(181,123,255,.07);
      border:        1px solid rgba(181,123,255,.18);
      border-radius: 6px;
      padding:       5px 9px;
      font-size:     11px;
    }
    .rand-fname { color: var(--purple); font-weight: 500; }
    .rand-type  { color: var(--muted); font-size: 10px; margin-top: 1px; }
    .rand-hash  { color: var(--code); font-family: 'IBM Plex Mono', monospace; font-size: 10px; margin-top: 2px; word-break: break-all; }
    .rand-more  { color: var(--muted); font-size: 12px; align-self: center; padding: 4px 8px; }

    /* ── Panel ── */
    .panel {
      background:    var(--panel);
      border:        1px solid var(--border);
      border-radius: 12px;
      padding:       1.25rem;
    }
    .panel-title {
      font-size:      11px;
      font-weight:    500;
      letter-spacing: 1.5px;
      text-transform: uppercase;
      color:          var(--muted);
      margin-bottom:  1rem;
      display:        flex;
      align-items:    center;
      gap:            8px;
    }
    .panel-title::before {
      content:       '';
      width:         3px;
      height:        12px;
      background:    var(--accent);
      border-radius: 2px;
      display:       block;
    }

    /* ── Form ── */
    .row3 {
      display:               grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap:                   10px;
      margin-bottom:         12px;
    }
    .field label { font-size: 12px; color: var(--muted); display: block; margin-bottom: 5px; letter-spacing: .5px; }
    select {
      width:         100%;
      background:    #161a20;
      border:        1px solid var(--border2);
      border-radius: 6px;
      color:         var(--text);
      padding:       8px 10px;
      font-size:     14px;
      font-family:   inherit;
      cursor:        pointer;
    }
    select:focus { outline: none; border-color: var(--accent); }

    /* ── Hint ── */
    .hint {
      background:    rgba(255,184,0,.06);
      border:        1px solid rgba(255,184,0,.2);
      border-radius: 8px;
      padding:       .75rem 1rem;
      margin-bottom: 12px;
      font-size:     12px;
      color:         var(--warn);
      line-height:   1.6;
    }

    /* ── Dropzone ── */
    .dropzone {
      border:        1.5px dashed var(--border2);
      border-radius: 8px;
      padding:       1.5rem 1rem;
      text-align:    center;
      cursor:        pointer;
      transition:    all .2s;
      position:      relative;
      margin-bottom: 12px;
    }
    .dropzone:hover,
    .dropzone.over   { border-color: var(--accent); background: rgba(0,229,160,.04); }
    .dropzone input  { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; height: 100%; }
    .dz-icon         { font-size: 1.75rem; margin-bottom: 6px; }
    .dropzone p      { font-size: 13px; color: var(--muted); }
    .dropzone .sel   { color: var(--accent); font-size: 14px; font-weight: 500; }

    /* ── Buttons ── */
    .btn {
      display:       inline-flex;
      align-items:   center;
      gap:           8px;
      padding:       10px 20px;
      font-size:     14px;
      font-weight:   500;
      border-radius: 8px;
      cursor:        pointer;
      transition:    all .15s;
      border:        none;
      font-family:   inherit;
    }
    .btn-primary          { background: var(--accent); color: #000; }
    .btn-primary:hover    { opacity: .88; }
    .btn-primary:disabled { opacity: .35; cursor: not-allowed; }
    .btn-ghost            { background: transparent; color: var(--muted); border: 1px solid var(--border2); font-size: 13px; padding: 7px 14px; }
    .btn-ghost:hover      { color: var(--text); }
    .btn-row { display: flex; justify-content: space-between; align-items: center; margin-top: 10px; }

    /* ── Result Box ── */
    .result {
      border-radius: 10px;
      padding:       1rem 1.25rem;
      margin-top:    1rem;
      display:       none;
      border:        1px solid;
      animation:     fadeIn .25s ease;
    }
    .result.show             { display: block; }
    .result.dup              { background: rgba(255,77,77,.07);  border-color: rgba(255,77,77,.3); }
    .result.new              { background: rgba(0,229,160,.07);  border-color: rgba(0,229,160,.3); }
    .result-head             { font-size: 16px; font-weight: 600; margin-bottom: 4px; }
    .result.dup .result-head { color: var(--danger); }
    .result.new .result-head { color: var(--accent); }
    .result-sub              { font-size: 12px; color: var(--muted); }

    /* ── Latency Row ── */
    .lat-row { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
    .lat-box {
      flex:          1;
      min-width:     80px;
      background:    rgba(255,255,255,.04);
      border:        1px solid var(--border2);
      border-radius: 8px;
      padding:       8px;
      text-align:    center;
    }
    .lat-num { font-family: 'IBM Plex Mono', monospace; font-size: 15px; font-weight: 500; }
    .lat-g   { color: var(--accent); }
    .lat-b   { color: var(--accent2); }
    .lat-o   { color: var(--warn); }
    .hash-box {
      font-family: 'IBM Plex Mono', monospace;
      font-size:   11px;
      color:       var(--code);
      margin-top:  8px;
      word-break:  break-all;
    }

    /* ── Steps ── */
    .steps { margin-top: 12px; display: flex; flex-direction: column; gap: 8px; }
    .step {
      display:       flex;
      align-items:   flex-start;
      gap:           10px;
      padding:       10px 12px;
      border-radius: 8px;
      border:        1px solid var(--border);
      font-size:     12px;
      position:      relative;
    }
    .step-con {
      position:       absolute;
      left:           28px;
      top:            36px;
      width:          1px;
      height:         calc(100% + 8px);
      background:     var(--border);
      pointer-events: none;
    }
    .steps .step:last-child .step-con { display: none; }
    .step-num {
      width:           22px;
      height:          22px;
      border-radius:   50%;
      display:         flex;
      align-items:     center;
      justify-content: center;
      font-weight:     600;
      font-size:       11px;
      flex-shrink:     0;
      margin-top:      1px;
    }
    .step-body   { flex: 1; min-width: 0; }
    .step-title  { font-weight: 600; margin-bottom: 3px; font-size: 13px; }
    .step-detail { font-family: 'IBM Plex Mono', monospace; color: var(--code); font-size: 11px; margin-bottom: 4px; word-break: break-all; }
    .step-note   { color: var(--muted); line-height: 1.5; }

    /* ── Bits Display ── */
    .bits     { display: flex; align-items: center; gap: 4px; margin: 6px 0; flex-wrap: wrap; }
    .bits-lbl { font-size: 10px; color: var(--muted); }
    .bit {
      width:           22px;
      height:          22px;
      display:         flex;
      align-items:     center;
      justify-content: center;
      border-radius:   4px;
      font-family:     'IBM Plex Mono', monospace;
      font-size:       11px;
      font-weight:     600;
    }
    .bit-1 { background: rgba(255,77,77,.18);  color: var(--danger); }
    .bit-0 { background: rgba(0,229,160,.12);  color: var(--accent); }

    /* ── Step Variants ── */
    .s-info             { background: rgba(0,153,255,.06);  border-color: rgba(0,153,255,.2); }
    .s-info .step-num   { background: rgba(0,153,255,.15);  color: var(--accent2); }
    .s-info .step-title { color: var(--accent2); }
    .s-new              { background: rgba(0,229,160,.06);  border-color: rgba(0,229,160,.2); }
    .s-new  .step-num   { background: rgba(0,229,160,.15);  color: var(--accent); }
    .s-new  .step-title { color: var(--accent); }
    .s-warn             { background: rgba(255,184,0,.06);  border-color: rgba(255,184,0,.2); }
    .s-warn .step-num   { background: rgba(255,184,0,.15);  color: var(--warn); }
    .s-warn .step-title { color: var(--warn); }
    .s-dup              { background: rgba(255,77,77,.06);   border-color: rgba(255,77,77,.2); }
    .s-dup  .step-num   { background: rgba(255,77,77,.15);   color: var(--danger); }
    .s-dup  .step-title { color: var(--danger); }

    /* ── Stats Panel ── */
    .stat-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: .75rem; }
    .stat {
      flex:          1;
      min-width:     70px;
      background:    rgba(255,255,255,.03);
      border:        1px solid var(--border);
      border-radius: 8px;
      padding:       10px;
      text-align:    center;
    }
    .stat-num { font-family: 'IBM Plex Mono', monospace; font-size: 17px; font-weight: 600; color: var(--accent); }
    .stat-lbl { font-size: 10px; color: var(--muted); margin-top: 3px; text-transform: uppercase; letter-spacing: .5px; }

    /* ── File List ── */
    .file-item {
      display:         flex;
      justify-content: space-between;
      align-items:     center;
      padding:         8px 10px;
      border-radius:   6px;
      margin-bottom:   6px;
      background:      rgba(255,255,255,.03);
      border:          1px solid var(--border);
      font-size:       12px;
    }
    .file-item.seed { border-color: rgba(0,229,160,.25); background: rgba(0,229,160,.04); }
    .file-left      { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
    .file-name      { font-weight: 500; color: var(--text); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 185px; }
    .file-hash      { font-family: 'IBM Plex Mono', monospace; color: var(--code); font-size: 10px; }
    .file-right     { display: flex; flex-direction: column; align-items: flex-end; gap: 3px; flex-shrink: 0; }
    .badge          { font-size: 10px; padding: 2px 7px; border-radius: 4px; font-weight: 500; }
    .b-seed         { background: rgba(0,229,160,.18);  color: var(--accent); }
    .b-doc          { background: rgba(0,153,255,.15);  color: #6ab0ff; }
    .b-img          { background: rgba(0,229,160,.1);   color: var(--accent); }
    .b-code         { background: rgba(255,184,0,.12);  color: var(--warn); }
    .file-size      { font-size: 10px; color: var(--muted); }
    .empty          { font-size: 13px; color: var(--muted); text-align: center; padding: 2rem 0; }

    /* ── Animation ── */
    @keyframes fadeIn {
      from { opacity: 0; transform: translateY(4px); }
      to   { opacity: 1; transform: none; }
    }
  </style>
</head>
<body>

  <!-- Header -->
  <div class="header">
    <div class="logo">SD</div>
    <div class="header-title">
      <h1>SDDaaS Demo v7 — Scale Test: 5,000 Random Hashes/Client</h1>
      <p>3-level tree: Dept → Employee → File Type → Bloom Filter &nbsp;|&nbsp; BF keyed per employee &nbsp;|&nbsp; Template-clone seeding</p>
    </div>
  </div>

  <!-- Scale Banner -->
  <div class="scale-banner">
    <div class="sc">
      <div class="sc-num" id="sc-emp">—</div>
      <div class="sc-lbl">Clients</div>
    </div>
    <div class="sc-div"></div>
    <div class="sc">
      <div class="sc-num" id="sc-dept">—</div>
      <div class="sc-lbl">Departments</div>
    </div>
    <div class="sc-div"></div>
    <div class="sc">
      <div class="sc-num">3</div>
      <div class="sc-lbl">Real Files / Client</div>
    </div>
    <div class="sc-div"></div>
    <div class="sc">
      <div class="sc-num" style="color:var(--purple)">5,000</div>
      <div class="sc-lbl">Random Hashes / Client</div>
    </div>
    <div class="sc-div"></div>
    <div class="sc">
      <div class="sc-num purple" id="sc-total-entries">—</div>
      <div class="sc-lbl">Total BF Items</div>
    </div>
    <div class="sc-div"></div>
    <div class="sc">
      <div class="sc-num" id="sc-used" style="color:var(--accent2)">—</div>
      <div class="sc-lbl">Storage Used</div>
    </div>
    <div class="sc-div"></div>
    <div class="sc">
      <div class="sc-num" id="sc-ms" style="color:var(--warn)">—</div>
      <div class="sc-lbl">Seed Time</div>
    </div>
  </div>

  <!-- Real Seeded Files Bar -->
  <div class="seed-bar">
    <div class="seed-bar-title">Real files pre-loaded for every client (1 physical copy each)</div>
    <div class="seed-files" id="seed-files-list"></div>
  </div>

  <!-- Random Hashes Bar -->
  <div class="rand-bar">
    <div class="rand-bar-title">
      🎲 Random pre-seeded hashes per client (scale simulation)
      <span class="rand-count-pill" id="rand-count-pill">5,000 × N clients</span>
    </div>
    <div class="rand-files" id="rand-files-list">
      <div class="rand-more">Loading...</div>
    </div>
  </div>

  <!-- Main Layout -->
  <div class="layout">

    <!-- Left: Upload + Result -->
    <div>
      <div class="panel" style="margin-bottom:1.25rem">
        <div class="panel-title">Upload &amp; Deduplication Check</div>
        <div class="hint">
          💡 <strong>Same file, same employee</strong> = duplicate detected (even among 5,003 pre-seeded entries).<br>
          💡 <strong>Same file, different employee</strong> = new file for that employee — per-client isolation holds at scale.<br>
          💡 Try uploading one of the 3 real seeded files above to verify lookup still works at 250M+ entry scale.
        </div>
        <div class="row3">
          <div class="field">
            <label>DEPARTMENT</label>
            <select id="sel-dept" onchange="onDeptChange()"><option>Loading...</option></select>
          </div>
          <div class="field">
            <label>EMPLOYEE ID</label>
            <select id="sel-emp"><option>Loading...</option></select>
          </div>
          <div class="field">
            <label>FILE TYPE</label>
            <select id="sel-type">
              <option>Document</option>
              <option>Image</option>
              <option>Code</option>
              <option>Video</option>
              <option>Archive</option>
              <option>Financial</option>
            </select>
          </div>
        </div>
        <div class="dropzone" id="dropzone">
          <input type="file" id="file-input" multiple>
          <div class="dz-icon">📂</div>
          <p id="dz-text">Click or drag files here (supports multiple files)</p>
        </div>
        <div class="btn-row">
          <button class="btn btn-ghost" onclick="resetSystem()">&#8635; Reset</button>
          <button class="btn btn-primary" id="btn-upload" onclick="uploadFile()" disabled style="padding:10px 28px">
            Upload &amp; Check &rarr;
          </button>
        </div>
      </div>

      <!-- Result Box -->
      <div class="result" id="result-box">
        <div class="result-head" id="result-head"></div>
        <div class="result-sub"  id="result-sub"></div>
        <div class="lat-row"     id="lat-row"></div>
        <div class="hash-box"    id="result-hash"></div>
        <div class="steps"       id="steps"></div>
      </div>
    </div>

    <!-- Right: Storage Stats -->
    <div>
      <div class="panel">
        <div class="panel-title">Storage Stats</div>
        <div class="stat-row">
          <div class="stat">
            <div class="stat-num" id="s-total">—</div>
            <div class="stat-lbl">Unique Files</div>
          </div>
          <div class="stat">
            <div class="stat-num" id="s-dup" style="color:var(--danger)">0</div>
            <div class="stat-lbl">Duplicates</div>
          </div>
          <div class="stat">
            <div class="stat-num" id="s-used" style="color:var(--accent2)">—</div>
            <div class="stat-lbl">Storage Used</div>
          </div>
          <div class="stat">
            <div class="stat-num" id="s-saved" style="color:var(--warn)">0 B</div>
            <div class="stat-lbl">Space Saved</div>
          </div>
          <div class="stat">
            <div class="stat-num" id="s-bf-items" style="color:var(--purple)">—</div>
            <div class="stat-lbl">BF Items</div>
          </div>
        </div>
        <div class="panel-title" style="margin-top:.5rem">Files in Storage</div>
        <div id="file-list"><div class="empty">No files yet.</div></div>
      </div>
    </div>

  </div><!-- /.layout -->

<script>
let dupCount = 0, savedBytes = 0, initUsedBytes = 0;
let deptEmpMap = {};

// ── Init ──
async function loadInit() {
  const r = await fetch('/init');
  const d = await r.json();

  document.getElementById('sc-emp').textContent           = d.employees.toLocaleString();
  document.getElementById('sc-dept').textContent          = d.departments;
  document.getElementById('sc-used').textContent          = fmtSize(d.used_bytes);
  document.getElementById('sc-ms').textContent            = d.seed_time_ms + ' ms';
  document.getElementById('sc-total-entries').textContent = (d.total_bf_items || 0).toLocaleString();
  document.getElementById('rand-count-pill').textContent  =
    `5,000 × ${d.employees.toLocaleString()} = ${(d.n_rand_hashes || 0).toLocaleString()} entries`;
  initUsedBytes = d.used_bytes;

  document.getElementById('s-total').textContent    = d.stored_unique;
  document.getElementById('s-used').textContent     = fmtSize(d.used_bytes);
  document.getElementById('s-bf-items').textContent = (d.total_bf_items || 0).toLocaleString();

  // Real seeded files bar
  document.getElementById('seed-files-list').innerHTML = d.seeded_files.map(f => `
    <div class="seed-file">
      <div class="seed-name">${f.filename}</div>
      <div class="seed-size">${fmtSize(f.size)}</div>
      <div class="seed-hash">${f.hash.slice(0, 32)}&#8230;</div>
    </div>`
  ).join('');

  // Random hashes sample (first 8)
  const samples = d.sample_hashes || [];
  document.getElementById('rand-files-list').innerHTML =
    samples.slice(0, 8).map(h => `
      <div class="rand-file">
        <div class="rand-fname">${h.filename}</div>
        <div class="rand-type">${h.type} &nbsp;·&nbsp; ${fmtSize(h.size)}</div>
        <div class="rand-hash">${h.hash.slice(0, 24)}&#8230;</div>
      </div>`
    ).join('') +
    `<div class="rand-more">+${(5000 - 8).toLocaleString()} more per client…</div>`;

  // Dept / Emp dropdowns
  deptEmpMap = d.dept_emp_map;
  const depts = Object.keys(deptEmpMap).sort();
  document.getElementById('sel-dept').innerHTML = depts.map(dep =>
    `<option value="${dep}">${dep} (${deptEmpMap[dep].length} emp)</option>`
  ).join('');
  populateEmps(depts[0]);
  renderFiles(d.store);
}
loadInit();

function populateEmps(dept) {
  document.getElementById('sel-emp').innerHTML =
    (deptEmpMap[dept] || []).map(e => `<option value="${e}">${e}</option>`).join('');
}

function onDeptChange() {
  populateEmps(document.getElementById('sel-dept').value);
}

// ── Drag & Drop ──
const dz  = document.getElementById('dropzone');
const fi  = document.getElementById('file-input');
const btn = document.getElementById('btn-upload');
let selectedFiles = [];

fi.addEventListener('change', e => {
  if (e.target.files.length) pickFiles([...e.target.files]);
});
dz.addEventListener('dragover',  e => { e.preventDefault(); dz.classList.add('over'); });
dz.addEventListener('dragleave', ()  => dz.classList.remove('over'));
dz.addEventListener('drop', e => {
  e.preventDefault();
  dz.classList.remove('over');
  if (e.dataTransfer.files.length) pickFiles([...e.dataTransfer.files]);
});

function pickFiles(files) {
  selectedFiles = files;
  if (files.length === 1) {
    document.getElementById('dz-text').innerHTML =
      `<span class="sel">&#10003; ${files[0].name} (${fmtSize(files[0].size)})</span>`;
  } else {
    const total = files.reduce((a, f) => a + f.size, 0);
    document.getElementById('dz-text').innerHTML =
      `<span class="sel">&#10003; ${files.length} files selected (${fmtSize(total)} total)</span>`;
  }
  btn.disabled = false;
}

// ── Utilities ──
async function sha256hex(buf) {
  const h = await crypto.subtle.digest('SHA-256', buf);
  return Array.from(new Uint8Array(h))
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

function fmtSize(b) {
  if (!b && b !== 0) return '—';
  if (b < 1024)       return b + ' B';
  if (b < 1048576)    return (b / 1024).toFixed(1) + ' KB';
  if (b < 1073741824) return (b / 1048576).toFixed(2) + ' MB';
  return (b / 1073741824).toFixed(2) + ' GB';
}

// ── Upload ──
async function uploadFile() {
  if (!selectedFiles.length) return;
  btn.disabled = true;

  const emp  = document.getElementById('sel-emp').value;
  const dept = document.getElementById('sel-dept').value;
  const ft   = document.getElementById('sel-type').value;

  if (selectedFiles.length === 1) {
    const f    = selectedFiles[0];
    const buf  = await f.arrayBuffer();
    const hash = await sha256hex(buf);
    const res  = await fetch('/process', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ hash, filename: f.name, size: f.size, emp, dept, type: ft }),
    });
    const data = await res.json();
    showResult(data, f.name, f.size);
  } else {
    const items = [];
    for (const f of selectedFiles) {
      const buf  = await f.arrayBuffer();
      const hash = await sha256hex(buf);
      items.push({ hash, filename: f.name, size: f.size });
    }
    const res  = await fetch('/process_batch', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ files: items, emp, dept, type: ft }),
    });
    const data = await res.json();
    showMultiResult(data.results);
    if (data.results.length) {
      const last = data.results[data.results.length - 1];
      document.getElementById('s-total').textContent    = last.stored_unique;
      document.getElementById('s-used').textContent     = fmtSize(last.used_bytes);
      document.getElementById('sc-used').textContent    = fmtSize(last.used_bytes);
      document.getElementById('s-bf-items').textContent = (last.total_bf_items || 0).toLocaleString();
      renderFiles(last.store);
    }
  }

  document.getElementById('dz-text').textContent = 'Click or drag files here (supports multiple files)';
  fi.value      = '';
  selectedFiles = [];
  btn.disabled  = true;
}

// ── Multi-result display ──
function showMultiResult(results) {
  const rb     = document.getElementById('result-box');
  const nDup   = results.filter(r => r.duplicate).length;
  const nNew   = results.length - nDup;
  const savedB = results.filter(r => r.duplicate).reduce((a, r) => a + r.size, 0);
  savedBytes += savedB;
  dupCount   += nDup;

  rb.className = 'result show ' + (
    nDup === results.length ? 'dup' :
    nNew === results.length ? 'new' : 'warn'
  );

  document.getElementById('result-head').textContent =
    `📦 Batch: ${results.length} files — ✅ ${nNew} new, 🔴 ${nDup} duplicate`;
  document.getElementById('result-sub').textContent =
    nDup > 0 ? `Space saved this batch: ${fmtSize(savedB)}` : 'All files stored successfully';
  document.getElementById('lat-row').innerHTML    = '';
  document.getElementById('result-hash').textContent = '';

  document.getElementById('steps').innerHTML = results.map((r, i) => {
    const icon = r.duplicate ? '🔴' : '🟢';
    const cls  = r.duplicate ? 's-dup' : 's-new';
    const note = r.duplicate
      ? 'Duplicate — 64-byte reference recorded'
      : 'Stored to cloud — inserted into Bloom Filter &amp; Metadata Index';
    return `<div class="step ${cls}">
      <div class="step-con"></div>
      <div class="step-num">${i + 1}</div>
      <div class="step-body">
        <div class="step-title">${icon} ${r.filename}</div>
        <div class="step-detail">${r.hash.slice(0, 32)}…</div>
        <div class="step-note">${note}</div>
      </div>
    </div>`;
  }).join('');

  document.getElementById('s-dup').textContent   = dupCount;
  document.getElementById('s-saved').textContent = fmtSize(savedBytes);
}

// ── Single-result display ──
function showResult(data, fname, size) {
  const rb = document.getElementById('result-box');
  rb.className = 'result show ' + (data.duplicate ? 'dup' : 'new');

  document.getElementById('result-head').textContent = data.duplicate
    ? '🔴 Duplicate Detected — file NOT stored again'
    : '🟢 New File Stored — saved successfully';
  document.getElementById('result-sub').textContent = data.duplicate
    ? `"${fname}" already exists for this employee — only a 64-byte reference recorded`
    : `"${fname}" inserted into this employee's Bloom Filter and Metadata Index`;

  const lr = document.getElementById('lat-row');
  if (data.total_us !== undefined) {
    lr.innerHTML =
      lb(`${data.total_us} µs`,          'Total Search', 'lat-g') +
      lb(`${data.bf_us   || '—'} µs`,    'Bloom Filter', 'lat-b') +
      lb(`${data.meta_us || '—'} µs`,    'Metadata',     'lat-o');
  }
  document.getElementById('result-hash').textContent = 'SHA-256:  ' + data.hash;

  const sc = { info: 's-info', new: 's-new', warn: 's-warn', dup: 's-dup' };
  document.getElementById('steps').innerHTML = data.steps.map(s => {
    const bHtml = s.bits
      ? `<div class="bits">
          <span class="bits-lbl">Bits:</span>
          ${s.bits.map(b => `<div class="bit ${b ? 'bit-1' : 'bit-0'}">${b}</div>`).join('')}
          <span class="bits-lbl">&nbsp;${s.bf_all_one ? '→ all 1s → probable dup' : '→ 0 found → definitely new'}</span>
        </div>`
      : '';
    return `<div class="step ${sc[s.status] || 's-info'}">
      <div class="step-con"></div>
      <div class="step-num">${s.step}</div>
      <div class="step-body">
        <div class="step-title">${s.title}</div>
        <div class="step-detail">${s.detail}</div>
        ${bHtml}
        <div class="step-note">${s.note}</div>
      </div>
    </div>`;
  }).join('');

  if (data.duplicate) { dupCount++; savedBytes += size; }
  document.getElementById('s-total').textContent    = data.stored_unique;
  document.getElementById('s-dup').textContent      = dupCount;
  document.getElementById('s-used').textContent     = fmtSize(data.used_bytes);
  document.getElementById('s-saved').textContent    = fmtSize(savedBytes);
  document.getElementById('sc-used').textContent    = fmtSize(data.used_bytes);
  document.getElementById('s-bf-items').textContent = (data.total_bf_items || 0).toLocaleString();
  renderFiles(data.store);
}

function lb(val, label, cls) {
  return `<div class="lat-box"><div class="lat-num ${cls}">${val}</div><div class="lat-lbl">${label}</div></div>`;
}

// ── File list renderer ──
function renderFiles(files) {
  const bc = {
    Document:  'b-doc',
    Image:     'b-img',
    Code:      'b-code',
    Video:     'b-img',
    Archive:   'b-doc',
    Financial: 'b-code',
  };
  const el = document.getElementById('file-list');
  if (!files || !files.length) {
    el.innerHTML = '<div class="empty">No files yet.</div>';
    return;
  }
  el.innerHTML = files.map(f => {
    const isSeed  = f.seeded;
    const badgeCls = isSeed ? 'b-seed' : (bc[f.type] || 'b-doc');
    const empLabel = f.emp && !isSeed ? `${f.dept}/${f.emp}` : '';
    return `<div class="file-item${isSeed ? ' seed' : ''}">
      <div class="file-left">
        <div class="file-name">${f.filename}</div>
        <div class="file-hash">${f.hash.slice(0, 22)}&#8230;</div>
      </div>
      <div class="file-right">
        <span class="badge ${badgeCls}">${isSeed ? 'pre-seeded' : f.type}</span>
        <span style="font-size:10px;color:var(--muted)">${empLabel}</span>
        <span class="file-size">${fmtSize(f.size)}</span>
      </div>
    </div>`;
  }).join('');
}

// ── Reset ──
async function resetSystem() {
  await fetch('/reset', { method: 'POST' });
  dupCount   = 0;
  savedBytes = 0;
  document.getElementById('result-box').className = 'result';
  document.getElementById('s-dup').textContent    = '0';
  document.getElementById('s-saved').textContent  = '0 B';
  await loadInit();
}
</script>
</body>
</html>"""

# ─────────────────────────────────────────────────────────────────
# HTTP Handler
# ─────────────────────────────────────────────────────────────────
engine = SDDaaS()

class Handler(BaseHTTPRequestHandler):
   def log_message(self, _fmt, *args):
       print(f"  [{args[1]}] {args[0]}")

   def do_GET(self):
       if self.path == "/init":
           summ = engine.storage_summary()
           self._json({
               "employees":    engine.n_employees,
               "departments":  len(engine.departments),
               "seed_time_ms": engine.seed_time_ms,
               "seeded_files": SEEDED_FILES,
               "dept_emp_map": engine.dept_emp_map,
               "store":        engine.get_store(),
               **summ,
           })
           return
       self.send_response(200)
       self.send_header("Content-Type", "text/html; charset=utf-8")
       self.end_headers()
       self.wfile.write(HTML_PAGE.encode())

   def do_POST(self):
       length = int(self.headers.get("Content-Length", 0))
       body   = json.loads(self.rfile.read(length))

       if self.path == "/process":
           result = engine.process_file(
               file_hash = body["hash"],
               filename  = body["filename"],
               size      = body["size"],
               emp_id    = body["emp"],
               file_type = body["type"],
               dept      = body.get("dept"),
           )
           summ = engine.storage_summary()
           result["stored_unique"]  = summ["stored_unique"]
           result["used_bytes"]     = summ["used_bytes"]
           result["total_bf_items"] = summ["total_bf_items"]
           result["store"]          = engine.get_store()
           self._json(result)

       elif self.path == "/process_batch":
           emp_id    = body["emp"]
           file_type = body["type"]
           dept      = body.get("dept")
           results   = []
           for item in body["files"]:
               r = engine.process_file(
                   file_hash = item["hash"],
                   filename  = item["filename"],
                   size      = item["size"],
                   emp_id    = emp_id,
                   file_type = file_type,
                   dept      = dept,
               )
               summ = engine.storage_summary()
               r["stored_unique"]  = summ["stored_unique"]
               r["used_bytes"]     = summ["used_bytes"]
               r["total_bf_items"] = summ["total_bf_items"]
               r["store"]          = engine.get_store()
               r["filename"]       = item["filename"]
               r["size"]           = item["size"]
               results.append(r)
           self._json({"results": results})

       elif self.path == "/reset":
           engine.reset()
           engine.seed(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "dataset.csv"))
           self._json({"ok": True})

   def _json(self, data):
       payload = json.dumps(data, ensure_ascii=False).encode()
       self.send_response(200)
       self.send_header("Content-Type", "application/json")
       self.send_header("Content-Length", len(payload))
       self.end_headers()
       self.wfile.write(payload)


# ─────────────────────────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
   SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
   CSV = os.path.join(SCRIPT_DIR, "..", "data", "dataset.csv")

   print("=" * 65)
   print("  SDDaaS Demo v7 — Scale Test")
   print(f"  Pre-seeding {TOTAL_PER_CLIENT:,} files/client "
         f"({len(SEEDED_FILES)} real + {N_RANDOM_HASHES:,} random hashes)...")
   engine.seed(CSV)
   total = engine.n_employees * TOTAL_PER_CLIENT
   print(f"  Scale: {engine.n_employees:,} clients × {TOTAL_PER_CLIENT:,} = {total:,} BF entries")
   print("=" * 65)

   PORT   = 8765
   server = HTTPServer(("localhost", PORT), Handler)
   url    = f"http://localhost:{PORT}"
   print(f"  Open  ->  {url}")
   print("  Stop with Ctrl+C")
   print("=" * 65)

   threading.Timer(0.9, lambda: webbrowser.open(url)).start()
   try:
       server.serve_forever()
   except KeyboardInterrupt:
       print("\nServer stopped.")