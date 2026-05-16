import math, hashlib, json, threading, webbrowser, time, csv, os, random, struct, io
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from collections import Counter

# ─────────────────────────────────────────────────────────────────
# Pre-seeded real files
# ─────────────────────────────────────────────────────────────────
SEEDED_FILES = [
   {
       "filename": "CloudCS.pdf",
       "hash":     "9f4d76176bb4c1a24503d223b2a31980bbb5df02ae12a25ca2b06693da55e9cc",
       "size":     43_003_354,
       "type":     "Document",
   },
   {
       "filename": "CybersecCS.pdf",
       "hash":     "e6e575b39e5f3d59d7bc2473cb96734f726ad31d777b3f998de5d9321f1f488b",
       "size":     40_403_384,
       "type":     "Document",
   },
   {
       "filename": "Ch15-ISO27001-2022_Lecture_DrSomchart.pdf",
       "hash":     "23ade9cd056c61f77358992508973c862334e5df7273cfd02b9139ca95a464c3",
       "size":     6_133_003,
       "type":     "Document",
   },
]


SEEDED_BYTES     = sum(f["size"] for f in SEEDED_FILES)
N_RANDOM_HASHES  = 5_000
TOTAL_PER_CLIENT = len(SEEDED_FILES) + N_RANDOM_HASHES
FILE_TYPES       = ["Document", "Image", "Code", "Video", "Archive", "Financial"]




def _gen_random_hashes(n: int, seed: int = 42) -> list:
   rng = random.Random(seed)
   out = []
   weights = [40, 20, 15, 10, 8, 7]
   for i in range(n):
       raw = struct.pack(">QQ", rng.getrandbits(64), rng.getrandbits(64))
       h   = hashlib.sha256(raw).hexdigest()
       ft  = rng.choices(FILE_TYPES, weights=weights, k=1)[0]
       sz  = rng.randint(4096, 500 * 1024 * 1024)
       out.append({"hash": h, "filename": f"rand_file_{i+1:05d}", "size": sz, "type": ft})
   return out


RANDOM_HASHES = _gen_random_hashes(N_RANDOM_HASHES)


# ─────────────────────────────────────────────────────────────────
# Multipart parser 
# ─────────────────────────────────────────────────────────────────
def parse_multipart(content_type: str, body: bytes) -> list:

   # Extract boundary from Content-Type header
   boundary = None
   for token in content_type.split(";"):
       token = token.strip()
       if token.startswith("boundary="):
           boundary = token[9:].strip().strip('"')
           break
   if not boundary:
       return []


   sep      = b"--" + boundary.encode()
   end_sep  = sep + b"--"
   parts    = []


   # Split on boundary — memoryview avoids copies
   segments = body.split(sep)
   for seg in segments[1:]:           # skip preamble before first boundary
       if seg.startswith(b"--"):      # final boundary
           break
       # Each segment: \r\n<headers>\r\n\r\n<data>\r\n
       if seg.startswith(b"\r\n"):
           seg = seg[2:]
       if seg.endswith(b"\r\n"):
           seg = seg[:-2]


       # Split headers from body on first blank line
       header_end = seg.find(b"\r\n\r\n")
       if header_end == -1:
           continue
       raw_headers = seg[:header_end].decode("latin-1")
       data        = seg[header_end + 4:]


       # Parse headers into dict
       headers = {}
       for line in raw_headers.split("\r\n"):
           if ":" in line:
               k, v = line.split(":", 1)
               headers[k.strip().lower()] = v.strip()


       # Parse Content-Disposition params
       cd = headers.get("content-disposition", "")
       params = {}
       for item in cd.split(";"):
           item = item.strip()
           if "=" in item:
               k, v = item.split("=", 1)
               params[k.strip()] = v.strip().strip('"')


       parts.append({
           "name":         params.get("name", ""),
           "filename":     params.get("filename", ""),
           "data":         data,
           "content_type": headers.get("content-type", "application/octet-stream"),
       })
   return parts



# ─────────────────────────────────────────────────────────────────
# Bloom Filter
# ─────────────────────────────────────────────────────────────────
class BloomFilter:
   def __init__(self, capacity=TOTAL_PER_CLIENT, fpr=0.01):
       capacity  = max(capacity, 1)
       self.m    = math.ceil(-capacity * math.log(fpr) / (math.log(2) ** 2))
       self.k    = max(1, math.ceil((self.m / capacity) * math.log(2)))
       self.bits = bytearray(self.m)
       self.count = 0


   def _hashes(self, item: str):
       # Encode once, derive two seeds via SHA-256 (single call, split digest)
       digest = hashlib.sha256(item.encode()).digest()
       h1 = int.from_bytes(digest[:8],  "big")
       h2 = int.from_bytes(digest[8:16], "big") | 1  # keep odd to ensure full coverage
       m  = self.m
       return [(h1 + i * h2) % m for i in range(self.k)]


   def insert(self, item: str):
       for idx in self._hashes(item):
           self.bits[idx] = 1
       self.count += 1


   def query(self, item: str):
       idxs = self._hashes(item)
       vals = [self.bits[i] for i in idxs]
       return all(vals), vals


   def stats(self):
       return {"m": self.m, "k": self.k, "count": self.count}


   def clone(self):
       bf2 = BloomFilter.__new__(BloomFilter)
       bf2.m = self.m; bf2.k = self.k
       bf2.bits = bytearray(self.bits); bf2.count = self.count
       return bf2


# ─────────────────────────────────────────────────────────────────
# SDDaaS Engine
# ─────────────────────────────────────────────────────────────────
class SDDaaS:
   def __init__(self):
       self.tree          = {}
       self.metadata      = {}
       self.stored        = []
       self.seeded_bytes  = 0
       self.n_employees   = 0
       self.departments   = []
       self.seed_time_ms  = 0
       self.dept_emp_map  = {}
       self.emp_dept_map  = {}
       self.n_rand_hashes = 0
       self.sample_hashes = []


   def _get_or_create_bf(self, emp_id: str, file_type: str) -> BloomFilter:
       self.tree.setdefault(emp_id, {})
       if file_type not in self.tree[emp_id]:
           self.tree[emp_id][file_type] = BloomFilter()
       return self.tree[emp_id][file_type]


   def seed(self, csv_path: str):
       if not os.path.exists(csv_path):
           print(f"  [WARN] {csv_path} not found"); return


       with open(csv_path, newline="") as f:
           rows = list(csv.reader(f))[1:]


       self.n_employees = len(rows)
       self.dept_emp_map = {}; self.emp_dept_map = {}
       for emp, dept in rows:
           self.dept_emp_map.setdefault(dept, []).append(emp)
           self.emp_dept_map[emp] = dept
       self.departments = sorted(self.dept_emp_map.keys())


       t0 = time.perf_counter()


       # Template BFs per file type
       templates = {}
       for ft in FILE_TYPES:
           tbf = BloomFilter()
           for sf in SEEDED_FILES:
               if sf["type"] == ft: tbf.insert(sf["hash"])
           for rh in RANDOM_HASHES:
               if rh["type"] == ft: tbf.insert(rh["hash"])
           templates[ft] = tbf


       # Clone per employee
       for emp, dept in rows:
           for ft, tbf in templates.items():
               self.tree.setdefault(emp, {})[ft] = tbf.clone()
           for sf in SEEDED_FILES:
               self.metadata[(emp, sf["type"], sf["hash"])] = True


       for sf in SEEDED_FILES:
           self.stored.append({
               "hash": sf["hash"], "filename": sf["filename"],
               "size": sf["size"], "emp": "ALL", "dept": "ALL",
               "type": sf["type"], "seeded": True,
           })


       self.seeded_bytes  = SEEDED_BYTES
       self.n_rand_hashes = self.n_employees * N_RANDOM_HASHES
       self.sample_hashes = RANDOM_HASHES[:10]
       self.seed_time_ms  = round((time.perf_counter() - t0) * 1000, 1)


       print(f"  [OK] {self.n_employees:,} employees × {TOTAL_PER_CLIENT:,} = "
             f"{self.n_employees * TOTAL_PER_CLIENT:,} BF entries — {self.seed_time_ms}ms")


   # ── single file (for 1-file detailed view) ──────────────────
   def process_file(self, file_hash, filename, size, emp_id, file_type, dept=None):
       dept_display = dept or self.emp_dept_map.get(emp_id, "?")
       steps = []
       t_start = time.perf_counter()


       steps.append({
           "step": 1, "title": "Search Tree Routing",
           "detail": f"Root → {dept_display} → {emp_id} → {file_type}",
           "note": f"O(1) 3-level routing. No scan across {self.n_employees:,} clients.",
           "status": "info",
       })


       bf = self._get_or_create_bf(emp_id, file_type)
       t_bf0 = time.perf_counter()
       bf_hit, bit_vals = bf.query(file_hash)
       t_bf_us = (time.perf_counter() - t_bf0) * 1e6
       bfstats = bf.stats()


       steps.append({
           "step": 2, "title": "Bloom Filter — Probabilistic Check",
           "detail": (f"Partition: {emp_id}/{file_type} | k={bfstats['k']} | "
                      f"m={bfstats['m']:,} bits | items={bfstats['count']:,} | "
                      f"query: {t_bf_us:.2f} µs"),
           "bits": bit_vals[:8], "bf_all_one": bf_hit,
           "note": ("All bits=1 → Probable Duplicate — verifying with Metadata."
                    if bf_hit else "Found 0-bit → Definitely NEW (zero false negatives)."),
           "status": "warn" if bf_hit else "new",
       })


       if not bf_hit:
           bf.insert(file_hash)
           self.metadata[(emp_id, file_type, file_hash)] = True
           self.stored.append({"hash": file_hash, "filename": filename, "size": size,
                                "emp": emp_id, "dept": dept_display, "type": file_type, "seeded": False})
           t_total = (time.perf_counter() - t_start) * 1e6
           steps.append({"step": 3, "title": "File Stored",
                          "detail": f"SHA-256: {file_hash}",
                          "note": "Inserted into BF + Metadata. Ciphertext saved to Cloud Storage.",
                          "status": "new"})
           return {"duplicate": False, "steps": steps, "hash": file_hash,
                   "total_us": round(t_total, 2), "bf_us": round(t_bf_us, 2)}


       t_m0 = time.perf_counter()
       exact = (emp_id, file_type, file_hash) in self.metadata
       t_meta_us = (time.perf_counter() - t_m0) * 1e6
       t_total = (time.perf_counter() - t_start) * 1e6


       steps.append({
           "step": 3, "title": "Metadata — Exact Match Verification",
           "detail": f"Lookup ({emp_id}, {file_type}, {file_hash[:16]}…) | {t_meta_us:.2f} µs",
           "note": ("Confirmed duplicate. NOT stored — 64-byte reference only."
                    if exact else "BF false positive. Treating as new."),
           "status": "dup" if exact else "new",
       })


       if not exact:
           bf.insert(file_hash)
           self.metadata[(emp_id, file_type, file_hash)] = True
           self.stored.append({"hash": file_hash, "filename": filename, "size": size,
                                "emp": emp_id, "dept": dept_display, "type": file_type, "seeded": False})


       return {"duplicate": exact, "steps": steps, "hash": file_hash,
               "total_us": round(t_total, 2), "bf_us": round(t_bf_us, 2), "meta_us": round(t_meta_us, 2)}


   # ── batch: browser already hashed, receive hash+metadata only ──
   def process_batch_hashes(self, file_list: list, emp_id: str, file_type: str, dept: str = None):

       dept_display = dept or self.emp_dept_map.get(emp_id, "?")
       bf = self._get_or_create_bf(emp_id, file_type)


       t_start      = time.perf_counter()
       results      = []
       n_dup        = 0
       n_new        = 0
       saved_b      = 0
       bf_ns        = 0.0
       meta_ns      = 0.0
       metadata     = self.metadata
       stored       = self.stored
       bf_query     = bf.query
       bf_insert    = bf.insert
       perf_counter = time.perf_counter


       for item in file_list:
           file_hash = item["hash"]
           filename  = item["filename"]
           size      = int(item.get("size", 0))


           t_b = perf_counter()
           bf_hit, _ = bf_query(file_hash)
           bf_ns    += perf_counter() - t_b


           if not bf_hit:
               bf_insert(file_hash)
               metadata[(emp_id, file_type, file_hash)] = True
               stored.append({"hash": file_hash, "filename": filename, "size": size,
                               "emp": emp_id, "dept": dept_display,
                               "type": file_type, "seeded": False})
               n_new += 1
               results.append({"hash": file_hash, "filename": filename,
                                 "size": size, "duplicate": False})
           else:
               t_m = perf_counter()
               exact = (emp_id, file_type, file_hash) in metadata
               meta_ns += perf_counter() - t_m
               if exact:
                   n_dup   += 1
                   saved_b += size
                   results.append({"hash": file_hash, "filename": filename,
                                    "size": size, "duplicate": True})
               else:
                   bf_insert(file_hash)
                   metadata[(emp_id, file_type, file_hash)] = True
                   stored.append({"hash": file_hash, "filename": filename, "size": size,
                                   "emp": emp_id, "dept": dept_display,
                                   "type": file_type, "seeded": False})
                   n_new += 1
                   results.append({"hash": file_hash, "filename": filename,
                                    "size": size, "duplicate": False})


       total_ms = (time.perf_counter() - t_start) * 1000
       n  = len(file_list)
       us = 1e6
       return {
           "results":     results,
           "n_dup":       n_dup,
           "n_new":       n_new,
           "n_total":     n,
           "saved_bytes": saved_b,
           "server_ms":   round(total_ms, 2),
           "avg_hash_us": 0.0,
           "avg_bf_us":   round(bf_ns   * us / max(n, 1), 2),
           "avg_meta_us": round(meta_ns * us / max(n, 1), 2),
       }


   def process_batch_files(self, file_items: list, emp_id: str, file_type: str, dept: str = None):
       dept_display = dept or self.emp_dept_map.get(emp_id, "?")
       bf = self._get_or_create_bf(emp_id, file_type)


       t_start  = time.perf_counter()
       results  = []
       n_dup    = 0
       n_new    = 0
       saved_b  = 0
       hash_ns  = 0
       bf_ns    = 0
       meta_ns  = 0


       # Cache frequently used locals for tight loop speed
       metadata      = self.metadata
       stored        = self.stored
       bf_query      = bf.query
       bf_insert     = bf.insert
       sha256        = hashlib.sha256
       perf_counter  = time.perf_counter


       for item in file_items:
           data     = item["data"]
           filename = item["filename"]
           size     = len(data)


           # Hash on server — sha256 of raw bytes (no hexdigest until needed)
           t_h = perf_counter()
           file_hash = sha256(data).hexdigest()
           hash_ns  += perf_counter() - t_h


           # BF check
           t_b = perf_counter()
           bf_hit, _ = bf_query(file_hash)
           bf_ns    += perf_counter() - t_b


           if not bf_hit:
               bf_insert(file_hash)
               metadata[(emp_id, file_type, file_hash)] = True
               stored.append({"hash": file_hash, "filename": filename, "size": size,
                               "emp": emp_id, "dept": dept_display,
                               "type": file_type, "seeded": False})
               n_new += 1
               results.append({"hash": file_hash, "filename": filename,
                                 "size": size, "duplicate": False})
           else:
               t_m = perf_counter()
               exact = (emp_id, file_type, file_hash) in metadata
               meta_ns += perf_counter() - t_m


               if exact:
                   n_dup   += 1
                   saved_b += size
                   results.append({"hash": file_hash, "filename": filename,
                                    "size": size, "duplicate": True})
               else:
                   bf_insert(file_hash)
                   metadata[(emp_id, file_type, file_hash)] = True
                   stored.append({"hash": file_hash, "filename": filename, "size": size,
                                   "emp": emp_id, "dept": dept_display,
                                   "type": file_type, "seeded": False})
                   n_new += 1
                   results.append({"hash": file_hash, "filename": filename,
                                    "size": size, "duplicate": False})


       total_ms = (time.perf_counter() - t_start) * 1000
       n = len(file_items)
       us = 1e6
       return {
           "results":     results,
           "n_dup":       n_dup,
           "n_new":       n_new,
           "n_total":     n,
           "saved_bytes": saved_b,
           "server_ms":   round(total_ms, 2),
           "avg_hash_us": round(hash_ns * us / max(n, 1), 2),
           "avg_bf_us":   round(bf_ns   * us / max(n, 1), 2),
           "avg_meta_us": round(meta_ns * us / max(n, 1), 2),
       }


   def get_store(self):
       uploads = [r for r in reversed(self.stored) if not r.get("seeded")]
       seeded  = [r for r in self.stored if r.get("seeded")]
       return (uploads + seeded)[:15]


   def storage_summary(self):
       new_bytes = sum(r["size"] for r in self.stored if not r.get("seeded"))
       return {
           "stored_unique": len(self.stored),
           "used_bytes":    self.seeded_bytes + new_bytes,
           "total_entries": self.n_employees * TOTAL_PER_CLIENT,
           "n_rand_hashes": self.n_rand_hashes,
           "sample_hashes": self.sample_hashes,
       }


   def reset(self):
       self.tree.clear(); self.metadata.clear(); self.stored.clear()
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
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SDDaaS v8 — Instant Batch</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@300;400;500;600&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #0a0c10;
  --panel: #111418;
  --border: #1e2228;
  --border2: #2a2f38;
  --accent: #00e5a0;
  --accent2: #0099ff;
  --warn: #ffb800;
  --danger: #ff4d4d;
  --purple: #b57bff;
  --text: #e8eaf0;
  --muted: #6b7280;
  --code: #a8c4ff;
}
* {
  box-sizing: border-box;
  margin: 0;
  padding: 0;
}
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'IBM Plex Sans',sans-serif;
  min-height: 100vh;
  padding: 2rem;
}
.wrap {
  max-width: 1280px;
  margin: 0 auto;
}
.layout {
  display: grid;
  grid-template-columns: 1fr 400px;
  gap: 1.5rem;
}
@media(max-width:960px) {
  .layout {
    grid-template-columns: 1fr;
  }
}

.header {
  display: flex;
  align-items: center;
  gap: 1rem;
  margin-bottom: 1.25rem;
}
.logo {
  width: 40px;
  height: 40px;
  background: var(--accent);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'IBM Plex Mono',monospace;
  font-weight: 600;
  color: #000;
  font-size: 13px;
  flex-shrink: 0;
}
.header-title h1 {
  font-size: 18px;
  font-weight: 600;
}
.header-title p {
  font-size: 12px;
  color: var(--muted);
  margin-top: 2px;
}

/* why-fast bar */
.why-bar {
  display: grid;
  grid-template-columns: 1fr auto 1fr auto 1fr auto 1fr;
  align-items: center;
  gap: 8px;
  margin-bottom: 1.25rem;
  background: rgba(0,229,160,.04);
  border: 1px solid rgba(0,229,160,.15);
  border-radius: 12px;
  padding: 1rem 1.25rem;
}
.why-step {
  text-align: center;
}
.why-num {
  font-family: 'IBM Plex Mono',monospace;
  font-size: 22px;
  font-weight: 600;
  color: var(--accent);
}
.why-lbl {
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}
.why-arr {
  color: var(--border2);
  font-size: 20px;
  text-align: center;
}
.why-bad {
  color: var(--danger);
}
.why-good {
  color: var(--accent);
}

/* compare box */
.compare {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 1.25rem;
}
.cmp {
  border-radius: 10px;
  padding: .875rem 1rem;
  border: 1px solid;
}
.cmp-old {
  border-color: rgba(255,77,77,.25);
  background: rgba(255,77,77,.05);
}
.cmp-new {
  border-color: rgba(0,229,160,.25);
  background: rgba(0,229,160,.05);
}
.cmp-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1px;
  text-transform: uppercase;
  margin-bottom: .5rem;
}
.cmp-old .cmp-title {
  color: var(--danger);
}
.cmp-new .cmp-title {
  color: var(--accent);
}
.cmp-row {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 3px;
  display: flex;
  align-items: flex-start;
  gap: 6px;
}
.cmp-row b {
  color: var(--text);
  flex-shrink: 0;
}

/* scale banner */
.scale-bar {
  display: flex;
  align-items: center;
  gap: 1.5rem;
  flex-wrap: wrap;
  background: linear-gradient(135deg,rgba(0,229,160,.07),rgba(0,153,255,.07));
  border: 1px solid rgba(0,229,160,.18);
  border-radius: 12px;
  padding: .875rem 1.5rem;
  margin-bottom: 1.25rem;
}
.sc {
  text-align: center;
}
.sc-num {
  font-family: 'IBM Plex Mono',monospace;
  font-size: 20px;
  font-weight: 600;
  color: var(--accent);
}
.sc-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 2px;
  letter-spacing: .5px;
  text-transform: uppercase;
}
.sc-div {
  width: 1px;
  height: 36px;
  background: var(--border2);
}

/* seed / rand bars */
.info-bar {
  border-radius: 10px;
  padding: .75rem 1.25rem;
  margin-bottom: 1.25rem;
  border: 1px solid;
}
.info-bar.green {
  border-color: rgba(0,229,160,.22);
  background: rgba(0,229,160,.04);
}
.info-bar.purple {
  border-color: rgba(181,123,255,.22);
  background: rgba(181,123,255,.04);
}
.info-bar-title {
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 1px;
  text-transform: uppercase;
  margin-bottom: .6rem;
}
.info-bar.green .info-bar-title {
  color: var(--accent);
}
.info-bar.purple .info-bar-title {
  color: var(--purple);
  display: flex;
  align-items: center;
  gap: 8px;
}
.rand-pill {
  background: rgba(181,123,255,.15);
  color: var(--purple);
  border-radius: 20px;
  padding: 2px 10px;
  font-size: 11px;
  font-family: 'IBM Plex Mono',monospace;
}
.chip-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.chip {
  border-radius: 6px;
  padding: 6px 10px;
  font-size: 12px;
}
.chip.green {
  background: rgba(0,229,160,.08);
  border: 1px solid rgba(0,229,160,.2);
}
.chip.purple {
  background: rgba(181,123,255,.07);
  border: 1px solid rgba(181,123,255,.18);
}
.chip-name {
  font-weight: 500;
}
.chip.green .chip-name {
  color: var(--accent);
}
.chip.purple .chip-name {
  color: var(--purple);
}
.chip-sub {
  color: var(--muted);
  font-size: 11px;
  margin-top: 1px;
  font-family: 'IBM Plex Mono',monospace;
}
.chip-hash {
  color: var(--code);
  font-size: 10px;
  margin-top: 2px;
  font-family: 'IBM Plex Mono',monospace;
  word-break: break-all;
}

/* panel */
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.25rem;
  margin-bottom: 1.25rem;
}
.panel-title {
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 1.5px;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 1rem;
  display: flex;
  align-items: center;
  gap: 8px;
}
.panel-title::before {
  content: '';
  width: 3px;
  height: 12px;
  background: var(--accent);
  border-radius: 2px;
}

.row3 {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 10px;
  margin-bottom: 12px;
}
.field label {
  font-size: 12px;
  color: var(--muted);
  display: block;
  margin-bottom: 5px;
  letter-spacing: .5px;
}
select {
  width: 100%;
  background: #161a20;
  border: 1px solid var(--border2);
  border-radius: 6px;
  color: var(--text);
  padding: 8px 10px;
  font-size: 14px;
  font-family: inherit;
  cursor: pointer;
}
select:focus {
  outline: none;
  border-color: var(--accent);
}

.hint {
  background: rgba(255,184,0,.06);
  border: 1px solid rgba(255,184,0,.2);
  border-radius: 8px;
  padding: .75rem 1rem;
  margin-bottom: 12px;
  font-size: 12px;
  color: var(--warn);
  line-height: 1.6;
}

/* dropzone */
.dropzone {
  border: 1.5px dashed var(--border2);
  border-radius: 8px;
  padding: 2rem 1rem;
  text-align: center;
  cursor: pointer;
  transition: all .2s;
  position: relative;
  margin-bottom: 12px;
}
.dropzone:hover,.dropzone.over {
  border-color: var(--accent);
  background: rgba(0,229,160,.04);
}
.dropzone input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
  width: 100%;
  height: 100%;
}
.dz-icon {
  font-size: 2rem;
  margin-bottom: 8px;
}
.dropzone p {
  font-size: 13px;
  color: var(--muted);
}
.dropzone .sel {
  color: var(--accent);
  font-size: 14px;
  font-weight: 500;
}
.dz-count {
  font-family: 'IBM Plex Mono',monospace;
  font-size: 24px;
  font-weight: 600;
  color: var(--accent);
  margin-bottom: 4px;
}

/* progress */
.progress-wrap {
  display: none;
  margin-bottom: 12px;
}
.progress-wrap.show {
  display: block;
}
.prog-label {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.prog-label span:last-child {
  font-family: 'IBM Plex Mono',monospace;
  font-size: 13px;
  color: var(--accent);
}
.prog-track {
  background: var(--border2);
  border-radius: 99px;
  height: 10px;
  overflow: hidden;
}
.prog-fill {
  background: linear-gradient(90deg,var(--accent),var(--accent2));
  height: 100%;
  width: 0%;
  transition: width .15s;
  border-radius: 99px;
}
.prog-sub {
  font-size: 11px;
  color: var(--muted);
  margin-top: 5px;
  font-family: 'IBM Plex Mono',monospace;
}

/* buttons */
.btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 22px;
  font-size: 14px;
  font-weight: 500;
  border-radius: 8px;
  cursor: pointer;
  transition: all .15s;
  border: none;
  font-family: inherit;
}
.btn-primary {
  background: var(--accent);
  color: #000;
}
.btn-primary:hover {
  opacity: .88;
}
.btn-primary:disabled {
  opacity: .3;
  cursor: not-allowed;
}
.btn-ghost {
  background: transparent;
  color: var(--muted);
  border: 1px solid var(--border2);
  font-size: 13px;
  padding: 7px 14px;
}
.btn-ghost:hover {
  color: var(--text);
}
.btn-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 10px;
}
.spin {
  display: inline-block;
  width: 13px;
  height: 13px;
  border: 2px solid rgba(0,0,0,.2);
  border-top-color: #000;
  border-radius: 50%;
  animation: spin .6s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* result */
.result {
  border-radius: 10px;
  padding: 1rem 1.25rem;
  margin-top: 1rem;
  display: none;
  border: 1px solid;
  animation: fadeIn .25s ease;
}
.result.show {
  display: block;
}
.result.dup {
  background: rgba(255,77,77,.07);
  border-color: rgba(255,77,77,.3);
}
.result.new {
  background: rgba(0,229,160,.07);
  border-color: rgba(0,229,160,.3);
}
.result.mixed {
  background: rgba(255,184,0,.05);
  border-color: rgba(255,184,0,.25);
}
.result-head {
  font-size: 16px;
  font-weight: 600;
  margin-bottom: 4px;
}
.result.dup .result-head {
  color: var(--danger);
}
.result.new .result-head {
  color: var(--accent);
}
.result.mixed .result-head {
  color: var(--warn);
}
.result-sub {
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 10px;
}

/* perf row */
.perf-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.pb {
  flex: 1;
  min-width: 70px;
  background: rgba(255,255,255,.04);
  border: 1px solid var(--border2);
  border-radius: 8px;
  padding: 8px;
  text-align: center;
}
.pb-num {
  font-family: 'IBM Plex Mono',monospace;
  font-size: 15px;
  font-weight: 600;
}
.p-g {
  color: var(--accent);
}
.p-b {
  color: var(--accent2);
}
.p-o {
  color: var(--warn);
}
.p-p {
  color: var(--purple);
}
.p-r {
  color: var(--danger);
}
.pb-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 2px;
}

/* batch summary */
.bsumgrid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  margin-bottom: 10px;
}
.bsum {
  background: rgba(255,255,255,.03);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  text-align: center;
}
.bsum-num {
  font-size: 22px;
  font-weight: 600;
  font-family: 'IBM Plex Mono',monospace;
}
.bsum-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 3px;
  text-transform: uppercase;
  letter-spacing: .5px;
}

.file-rows {
  max-height: 240px;
  overflow-y: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.fr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 7px 10px;
  border-bottom: 1px solid var(--border);
  font-size: 12px;
}
.fr:last-child {
  border-bottom: none;
}
.fr.is-dup {
  background: rgba(255,77,77,.04);
}
.fr.is-new {
  background: rgba(0,229,160,.03);
}
.fr-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 220px;
  font-weight: 500;
}
.fr-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.fr-hash {
  font-family: 'IBM Plex Mono',monospace;
  font-size: 10px;
  color: var(--code);
}
.badge {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 4px;
  font-weight: 600;
}
.b-dup {
  background: rgba(255,77,77,.15);
  color: var(--danger);
}
.b-new {
  background: rgba(0,229,160,.15);
  color: var(--accent);
}

/* steps (single file) */
.hash-box {
  font-family: 'IBM Plex Mono',monospace;
  font-size: 11px;
  color: var(--code);
  margin-bottom: 10px;
  word-break: break-all;
}
.steps {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.step {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 8px;
  border: 1px solid var(--border);
  font-size: 12px;
  position: relative;
}
.step-con {
  position: absolute;
  left: 27px;
  top: 36px;
  width: 1px;
  height: calc(100% + 8px);
  background: var(--border);
  pointer-events: none;
}
.steps .step:last-child .step-con {
  display: none;
}
.step-num {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 11px;
  flex-shrink: 0;
  margin-top: 1px;
}
.step-body {
  flex: 1;
  min-width: 0;
}
.step-title {
  font-weight: 600;
  margin-bottom: 3px;
  font-size: 13px;
}
.step-detail {
  font-family: 'IBM Plex Mono',monospace;
  color: var(--code);
  font-size: 11px;
  margin-bottom: 4px;
  word-break: break-all;
}
.step-note {
  color: var(--muted);
  line-height: 1.5;
}
.bits {
  display: flex;
  align-items: center;
  gap: 4px;
  margin: 6px 0;
  flex-wrap: wrap;
}
.bits-lbl {
  font-size: 10px;
  color: var(--muted);
}
.bit {
  width: 22px;
  height: 22px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  font-family: 'IBM Plex Mono',monospace;
  font-size: 11px;
  font-weight: 600;
}
.bit-1 {
  background: rgba(255,77,77,.18);
  color: var(--danger);
}
.bit-0 {
  background: rgba(0,229,160,.12);
  color: var(--accent);
}
.s-info {
  background: rgba(0,153,255,.06);
  border-color: rgba(0,153,255,.2);
}
.s-info .step-num {
  background: rgba(0,153,255,.15);
  color: var(--accent2);
}
.s-info .step-title {
  color: var(--accent2);
}
.s-new {
  background: rgba(0,229,160,.06);
  border-color: rgba(0,229,160,.2);
}
.s-new .step-num {
  background: rgba(0,229,160,.15);
  color: var(--accent);
}
.s-new .step-title {
  color: var(--accent);
}
.s-warn {
  background: rgba(255,184,0,.06);
  border-color: rgba(255,184,0,.2);
}
.s-warn .step-num {
  background: rgba(255,184,0,.15);
  color: var(--warn);
}
.s-warn .step-title {
  color: var(--warn);
}
.s-dup {
  background: rgba(255,77,77,.06);
  border-color: rgba(255,77,77,.2);
}
.s-dup .step-num {
  background: rgba(255,77,77,.15);
  color: var(--danger);
}
.s-dup .step-title {
  color: var(--danger);
}

/* stats panel */
.stat-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: .75rem;
}
.stat {
  flex: 1;
  min-width: 70px;
  background: rgba(255,255,255,.03);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  text-align: center;
}
.stat-num {
  font-family: 'IBM Plex Mono',monospace;
  font-size: 17px;
  font-weight: 600;
  color: var(--accent);
}
.stat-lbl {
  font-size: 10px;
  color: var(--muted);
  margin-top: 3px;
  text-transform: uppercase;
  letter-spacing: .5px;
}

/* file list */
.file-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border-radius: 6px;
  margin-bottom: 6px;
  background: rgba(255,255,255,.03);
  border: 1px solid var(--border);
  font-size: 12px;
}
.file-item.seed {
  border-color: rgba(0,229,160,.25);
  background: rgba(0,229,160,.04);
}
.file-left {
  min-width: 0;
}
.file-name {
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 185px;
}
.file-hash {
  font-family: 'IBM Plex Mono',monospace;
  color: var(--code);
  font-size: 10px;
  margin-top: 2px;
}
.file-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  flex-shrink: 0;
}
.b-seed {
  background: rgba(0,229,160,.18);
  color: var(--accent);
}
.b-doc {
  background: rgba(0,153,255,.15);
  color: #6ab0ff;
}
.b-img {
  background: rgba(0,229,160,.1);
  color: var(--accent);
}
.b-code {
  background: rgba(255,184,0,.12);
  color: var(--warn);
}
.file-size {
  font-size: 10px;
  color: var(--muted);
}
.empty {
  font-size: 13px;
  color: var(--muted);
  text-align: center;
  padding: 2rem 0;
}

@keyframes fadeIn {
  from {
    opacity: 0;
    transform: translateY(4px);
  }
  to {
    opacity: 1;
    transform: none;
  }
}

</style>
</head>
<body>
<div class="wrap">


<div class="header">
 <div class="logo">SD</div>
 <div class="header-title">
   <h1>SDDaaS v8 — Instant Batch Deduplication</h1>
   <p>FormData multipart → server-side hashing → zero per-file JS overhead</p>
 </div>
</div>


<!-- Scale banner -->
<div class="scale-bar">
 <div class="sc"><div class="sc-num" id="sc-emp">—</div><div class="sc-lbl">Clients</div></div>
 <div class="sc-div"></div>
 <div class="sc"><div class="sc-num" id="sc-dept">—</div><div class="sc-lbl">Departments</div></div>
 <div class="sc-div"></div>
 <div class="sc"><div class="sc-num" style="color:var(--purple)">5,003</div><div class="sc-lbl">Files/Client BF</div></div>
 <div class="sc-div"></div>
 <div class="sc"><div class="sc-num" id="sc-entries" style="color:var(--purple)">—</div><div class="sc-lbl">Total BF Entries</div></div>
 <div class="sc-div"></div>
</div>


<div class="info-bar green">
 <div class="info-bar-title">Real files pre-loaded for every client</div>
 <div class="chip-row" id="seed-list"></div>
</div>


<div class="info-bar purple">
 <div class="info-bar-title">
   🎲 Random pre-seeded hashes per client
   <span class="rand-pill" id="rand-pill">5,000 × N</span>
 </div>
 <div class="chip-row" id="rand-list"></div>
</div>


<div class="layout">
 <!-- Left -->
 <div>
   <div class="panel">
     <div class="panel-title">Upload & Instant Deduplication</div>
     <div class="hint">
        Drop <strong>1 file</strong> → full 3-step Bloom Filter pipeline shown.<br>
        Drop <strong>multiple files</strong> → identity hash (filename+size, no disk read) → JSON to server → BF check instantly.<br>
        Or click <strong>Simulate 5,000 Files</strong> to generate a batch with 500 duplicates automatically.
     </div>


     <div class="row3">
       <div class="field">
         <label>DEPARTMENT</label>
         <select id="sel-dept" onchange="onDeptChange()"></select>
       </div>
       <div class="field">
         <label>EMPLOYEE ID</label>
         <select id="sel-emp"></select>
       </div>
       <div class="field">
         <label>FILE TYPE</label>
         <select id="sel-type">
           <option>Document</option><option>Image</option><option>Code</option>
           <option>Video</option><option>Archive</option><option>Financial</option>
         </select>
       </div>
     </div>


     <div class="dropzone" id="dropzone">
       <input type="file" id="file-input" multiple>
       <div class="dz-icon">📂</div>
       <p id="dz-text">Click or drag files — any number, any size</p>
     </div>


     <!-- Upload progress -->
     <div class="progress-wrap" id="prog-wrap">
       <div class="prog-label">
         <span id="prog-lbl">Uploading…</span>
         <span id="prog-pct">—</span>
       </div>
       <div class="prog-track"><div class="prog-fill" id="prog-fill"></div></div>
       <div class="prog-sub" id="prog-sub"></div>
     </div>


     <div class="btn-row">
       <button class="btn btn-ghost" onclick="resetSystem()">↺ Reset</button>
       <button class="btn btn-primary" id="btn-up" onclick="doUpload()" disabled>
         Upload &amp; Check →
       </button>
     </div>
   </div>


   <div class="result" id="result-box">
     <div class="result-head" id="r-head"></div>
     <div class="result-sub"  id="r-sub"></div>
     <div class="perf-row"    id="r-perf"></div>
     <div id="r-detail"></div>
   </div>
 </div>


 <!-- Right -->
 <div>
   <div class="panel">
     <div class="panel-title">Storage Stats</div>
     <div class="stat-row">
       <div class="stat"><div class="stat-num" id="s-total">—</div><div class="stat-lbl">Unique Files</div></div>
       <div class="stat"><div class="stat-num" id="s-dup" style="color:var(--danger)">0</div><div class="stat-lbl">Duplicates</div></div>
       <div class="stat"><div class="stat-num" id="s-used" style="color:var(--accent2)">—</div><div class="stat-lbl">Storage Used</div></div>
       <div class="stat"><div class="stat-num" id="s-saved" style="color:var(--warn)">0 B</div><div class="stat-lbl">Space Saved</div></div>
     </div>
     <div class="panel-title" style="margin-top:.5rem">Files in Storage</div>
     <div id="file-list"><div class="empty">Loading…</div></div>
   </div>
 </div>
</div>


</div>
<script>
var deptEmpMap = {}, dupCount = 0, savedBytes = 0, selFiles = []


async function loadInit() {
 var d = await fetch('/init').then(r => r.json())
 document.getElementById('sc-emp').textContent     = d.employees.toLocaleString()
 document.getElementById('sc-dept').textContent    = d.departments
 document.getElementById('sc-used').textContent    = fmt(d.used_bytes)
 document.getElementById('sc-ms').textContent      = d.seed_time_ms + ' ms'
 document.getElementById('sc-entries').textContent = (d.total_entries||0).toLocaleString()
 document.getElementById('rand-pill').textContent  =
   '5,000 \xd7 ' + d.employees.toLocaleString() + ' = ' + (d.n_rand_hashes||0).toLocaleString()
 document.getElementById('s-total').textContent = d.stored_unique
 document.getElementById('s-used').textContent  = fmt(d.used_bytes)


 document.getElementById('seed-list').innerHTML = (d.seeded_files||[]).map(f =>
   '<div class="chip green"><div class="chip-name">' + f.filename + '</div>' +
   '<div class="chip-sub">' + fmt(f.size) + '</div>' +
   '<div class="chip-hash">' + f.hash.slice(0,28) + '\u2026</div></div>'
 ).join('')


 document.getElementById('rand-list').innerHTML =
   (d.sample_hashes||[]).slice(0,6).map(h =>
     '<div class="chip purple"><div class="chip-name">' + h.filename + '</div>' +
     '<div class="chip-sub">' + h.type + ' \xb7 ' + fmt(h.size) + '</div>' +
     '<div class="chip-hash">' + h.hash.slice(0,22) + '\u2026</div></div>'
   ).join('') + '<div style="color:var(--muted);font-size:12px;align-self:center;padding:4px 8px">+4,994 more\u2026</div>'


 deptEmpMap = d.dept_emp_map
 var depts  = Object.keys(deptEmpMap).sort()
 document.getElementById('sel-dept').innerHTML = depts.map(d =>
   '<option value="' + d + '">' + d + ' (' + deptEmpMap[d].length + ' emp)</option>'
 ).join('')
 onDeptChange()
 renderFiles(d.store)
}
loadInit()


function onDeptChange() {
 var dept = document.getElementById('sel-dept').value
 document.getElementById('sel-emp').innerHTML = (deptEmpMap[dept]||[]).map(e =>
   '<option value="' + e + '">' + e + '</option>'
 ).join('')
}


// ── file picker ──────────────────────────────────────────────
var dz  = document.getElementById('dropzone')
var fi  = document.getElementById('file-input')
var btn = document.getElementById('btn-up')


fi.addEventListener('change', function(e){ if(e.target.files.length) pick([].slice.call(e.target.files)) })
dz.addEventListener('dragover',  function(e){ e.preventDefault(); dz.classList.add('over') })
dz.addEventListener('dragleave', function(){ dz.classList.remove('over') })
dz.addEventListener('drop', function(e){
 e.preventDefault(); dz.classList.remove('over')
 if(e.dataTransfer.files.length) pick([].slice.call(e.dataTransfer.files))
})


function pick(files) {
 selFiles = files
 var total = files.reduce(function(a,f){ return a+f.size }, 0)
 if(files.length === 1) {
   document.getElementById('dz-text').innerHTML = '<span class="sel">\u2713 ' + files[0].name + ' (' + fmt(files[0].size) + ')</span>'
 } else {
   document.getElementById('dz-text').innerHTML =
     '<div class="dz-count">' + files.length.toLocaleString() + ' files</div>' +
     '<div class="sel">' + fmt(total) + ' total \u2014 ready to upload</div>'
 }
 btn.disabled = false
}


// ── simulate batch (no real files needed) ────────────────────
async function simulateBatch() {
 var btn_sim = document.getElementById('btn-sim')
 btn_sim.disabled = true
 btn_sim.textContent = '⏳ Running…'


 var emp  = document.getElementById('sel-emp').value
 var dept = document.getElementById('sel-dept').value
 var ft   = document.getElementById('sel-type').value


 var pw = document.getElementById('prog-wrap')
 pw.classList.add('show')
 document.getElementById('prog-fill').style.width = '5%'
 document.getElementById('prog-pct').textContent  = '5%'
 document.getElementById('prog-lbl').textContent  = 'Generating 5,000 synthetic file hashes…'
 document.getElementById('prog-sub').textContent  = 'No disk read — pure in-memory generation'


 var t0  = performance.now()
 var enc = new TextEncoder()


 // Generate 4500 unique + 500 duplicate hashes
 var N_TOTAL = 5000, N_UNIQUE = 4500
 var fileInfos = []
 var CHUNK = 250


 // unique files
 for (var start = 0; start < N_UNIQUE; start += CHUNK) {
   var end   = Math.min(start + CHUNK, N_UNIQUE)
   var batch = []
   for (var i = start; i < end; i++) {
     var seed   = enc.encode('synthfile_' + i + '|' + (1024 + i * 7))
     var digest = await crypto.subtle.digest('SHA-256', seed)
     var hex    = Array.from(new Uint8Array(digest)).map(function(b){ return b.toString(16).padStart(2,'0') }).join('')
     batch.push({ filename: 'file_' + String(i).padStart(5,'0') + '.bin', hash: hex, size: 1024 + i * 7 })
   }
   fileInfos = fileInfos.concat(batch)
   var pct = Math.round(fileInfos.length / N_TOTAL * 70)
   document.getElementById('prog-fill').style.width = pct + '%'
   document.getElementById('prog-pct').textContent  = pct + '%'
   document.getElementById('prog-sub').textContent  = fileInfos.length + ' / ' + N_TOTAL + ' generated…'
 }


 // 500 duplicates (reuse first 500 hashes with different filenames)
 for (var j = 0; j < 500; j++) {
   var orig = fileInfos[j]
   fileInfos.push({ filename: 'dup_copy_' + j + '.bin', hash: orig.hash, size: orig.size })
 }


 document.getElementById('prog-fill').style.width = '80%'
 document.getElementById('prog-pct').textContent  = '80%'
 document.getElementById('prog-lbl').textContent  = 'Sending ' + fileInfos.length + ' hashes to server…'


 var res    = await fetch('/upload_batch_json', {
   method:  'POST',
   headers: { 'Content-Type': 'application/json' },
   body:    JSON.stringify({ emp: emp, dept: dept, type: ft, files: fileInfos })
 })
 var result = await res.json()
 var rtt    = Math.round(performance.now() - t0)


 document.getElementById('prog-fill').style.width = '100%'
 document.getElementById('prog-pct').textContent  = '100%'
 document.getElementById('prog-lbl').textContent  = 'Done — ' + rtt + 'ms total'
 document.getElementById('prog-sub').textContent  =
   'Server: ' + result.server_ms + 'ms · ' + result.n_new + ' new · ' + result.n_dup + ' duplicates detected'


 showBatch(result, rtt)
 setTimeout(function(){ pw.classList.remove('show') }, 5000)
 btn_sim.disabled = false
 btn_sim.textContent = '⚡ Simulate 5,000 Files'
}


// ── upload ───────────────────────────────────────────────────
async function doUpload() {
 if(!selFiles.length) return
 btn.disabled = true


 var emp  = document.getElementById('sel-emp').value
 var dept = document.getElementById('sel-dept').value
 var ft   = document.getElementById('sel-type').value


 if(selFiles.length === 1) {
   // ── SINGLE FILE: detailed 3-step view ──────────────────
   btn.innerHTML = '<span class="spin"></span>Checking\u2026'
   var fd = new FormData()
   fd.append('emp', emp); fd.append('dept', dept); fd.append('type', ft)
   fd.append('file', selFiles[0])


   var t0  = performance.now()
   var res = await fetch('/upload_single', { method: 'POST', body: fd })
   var data = await res.json()
   var rtt = Math.round(performance.now() - t0)
   showSingle(data, rtt)
   btn.innerHTML = 'Upload &amp; Check \u2192'


 } else {
   // ── BATCH: generate deterministic hash from filename+size (no disk read) ──
   // Demo only needs identity hashes — actual content never leaves the file system.
   // This makes 5,000 files process in < 1s regardless of file size.
   var pw = document.getElementById('prog-wrap')
   pw.classList.add('show')
   document.getElementById('prog-lbl').textContent = 'Generating hashes for ' + selFiles.length.toLocaleString() + ' files…'
   document.getElementById('prog-sub').textContent = 'Identity hash from filename+size — no disk read, instant'
   document.getElementById('prog-fill').style.width = '10%'
   document.getElementById('prog-pct').textContent = '10%'


   var t0 = performance.now()


   // Fast deterministic hex hash: encode filename+size as UTF-8 bytes,
   // then SHA-256 via SubtleCrypto. No arrayBuffer() = no disk I/O.
   var enc = new TextEncoder()
   var CHUNK = 200
   var fileInfos = []
   for (var start = 0; start < selFiles.length; start += CHUNK) {
     var slice = Array.from(selFiles).slice(start, start + CHUNK)
     var batch = await Promise.all(slice.map(async function(f) {
       var seed   = enc.encode(f.name + '|' + f.size)
       var digest = await crypto.subtle.digest('SHA-256', seed)
       var hex    = Array.from(new Uint8Array(digest)).map(function(b){ return b.toString(16).padStart(2,'0') }).join('')
       return { filename: f.name, hash: hex, size: f.size }
     }))
     fileInfos = fileInfos.concat(batch)
     var pct = Math.min(80, Math.round(fileInfos.length / selFiles.length * 80))
     document.getElementById('prog-fill').style.width = pct + '%'
     document.getElementById('prog-pct').textContent  = pct + '%'
     document.getElementById('prog-sub').textContent  =
       fileInfos.length.toLocaleString() + ' / ' + selFiles.length.toLocaleString() + ' hashed (identity)'
   }


   document.getElementById('prog-fill').style.width = '85%'
   document.getElementById('prog-pct').textContent  = '85%'
   document.getElementById('prog-lbl').textContent  = 'Sending to server…'


   var res    = await fetch('/upload_batch_json', {
     method:  'POST',
     headers: { 'Content-Type': 'application/json' },
     body:    JSON.stringify({ emp: emp, dept: dept, type: ft, files: fileInfos })
   })
   var result = await res.json()
   var rtt    = Math.round(performance.now() - t0)


   document.getElementById('prog-fill').style.width = '100%'
   document.getElementById('prog-pct').textContent  = '100%'
   document.getElementById('prog-lbl').textContent  = 'Done'
   document.getElementById('prog-sub').textContent  =
     'Total: ' + rtt + 'ms — Server: ' + result.server_ms + 'ms — ' +
     result.n_total + ' files processed'


   showBatch(result, rtt)
   setTimeout(function(){ pw.classList.remove('show') }, 4000)
   btn.innerHTML = 'Upload &amp; Check \u2192'
 }


 document.getElementById('dz-text').textContent = 'Click or drag files \u2014 any number, any size'
 fi.value = ''; selFiles = []; btn.disabled = true
}


// ── result renderers ─────────────────────────────────────────
function showSingle(data, rtt) {
 var rb = document.getElementById('result-box')
 rb.className = 'result show ' + (data.duplicate ? 'dup' : 'new')
 document.getElementById('r-head').textContent = data.duplicate
   ? '\uD83D\uDD34 Duplicate Detected \u2014 file NOT stored again'
   : '\uD83D\uDFE2 New File Stored \u2014 saved successfully'
 document.getElementById('r-sub').textContent = data.duplicate
   ? '"' + data.filename + '" already exists for this employee \u2014 64-byte reference only'
   : '"' + data.filename + '" hashed + inserted into BF and Metadata on server'


 document.getElementById('r-perf').innerHTML =
   mkpb(rtt + ' ms', 'Round Trip', 'p-g') +
   mkpb((data.total_us||'\u2014') + ' \xb5s', 'Server Total', 'p-b') +
   mkpb((data.bf_us||'\u2014') + ' \xb5s', 'Bloom Filter', 'p-o') +
   mkpb((data.meta_us||'\u2014') + ' \xb5s', 'Metadata', 'p-p')


 var sc = {info:'s-info', new:'s-new', warn:'s-warn', dup:'s-dup'}
 document.getElementById('r-detail').innerHTML =
   '<div class="hash-box">SHA-256: ' + (data.hash||'') + '</div>' +
   '<div class="steps">' +
   (data.steps||[]).map(function(s){
     var bHtml = ''
     if(s.bits) {
       bHtml = '<div class="bits"><span class="bits-lbl">Bits:</span>' +
         s.bits.map(function(b){ return '<div class="bit '+(b?'bit-1':'bit-0')+'">'+b+'</div>' }).join('') +
         '<span class="bits-lbl"> \u2192 '+(s.bf_all_one?'all 1s \u2192 probable dup':'0 found \u2192 definitely new')+'</span></div>'
     }
     return '<div class="step '+(sc[s.status]||'s-info')+'">' +
       '<div class="step-con"></div>' +
       '<div class="step-num">'+s.step+'</div>' +
       '<div class="step-body">' +
         '<div class="step-title">'+s.title+'</div>' +
         '<div class="step-detail">'+s.detail+'</div>' + bHtml +
         '<div class="step-note">'+s.note+'</div>' +
       '</div></div>'
   }).join('') + '</div>'


 if(data.duplicate){ dupCount++; savedBytes += (data.size||0) }
 updateStats(data.stored_unique, data.used_bytes)
 renderFiles(data.store)
}


function showBatch(data, rtt) {
 var rb   = document.getElementById('result-box')
 var nDup = data.n_dup, nNew = data.n_new, n = data.n_total
 dupCount   += nDup
 savedBytes += data.saved_bytes


 rb.className = 'result show ' + (nDup===n?'dup':nNew===n?'new':'mixed')
 document.getElementById('r-head').textContent =
   '\uD83D\uDCE6 Batch: ' + n.toLocaleString() + ' files \u2014 \uD83D\uDFE2 ' +
   nNew.toLocaleString() + ' new, \uD83D\uDD34 ' + nDup.toLocaleString() + ' duplicate'
 document.getElementById('r-sub').textContent =
   'Space saved: ' + fmt(data.saved_bytes) + ' \u2014 duplicate rate: ' +
   Math.round(nDup/n*100) + '%'
 
 var rows = data.results||[]
 document.getElementById('r-detail').innerHTML =
   '<div class="bsumgrid">' +
   bsum(nNew.toLocaleString(), 'New Files Stored', 'var(--accent)') +
   bsum(nDup.toLocaleString(), 'Duplicates Blocked', 'var(--danger)') +
   bsum(fmt(data.saved_bytes), 'Space Saved', 'var(--warn)') +
   bsum(Math.round(nDup/n*100)+'%', 'Duplicate Rate', 'var(--purple)') +
   '</div>' +
   '<div class="file-rows">' +
   rows.slice(0,200).map(function(r){
     return '<div class="fr '+(r.duplicate?'is-dup':'is-new')+'">' +
       '<span class="fr-name">'+ r.filename +'</span>' +
       '<div class="fr-right">' +
         '<span class="fr-hash">'+ r.hash.slice(0,12) +'\u2026</span>' +
         '<span class="badge '+(r.duplicate?'b-dup':'b-new')+'">'+(r.duplicate?'dup':'new')+'</span>' +
       '</div></div>'
   }).join('') +
   (rows.length>200?'<div style="text-align:center;padding:8px;color:var(--muted);font-size:12px">+'+(rows.length-200)+' more\u2026</div>':'') +
   '</div>'


 updateStats(data.stored_unique, data.used_bytes)
 renderFiles(data.store)
}


function mkpb(val, lbl, cls) {
 return '<div class="pb"><div class="pb-num '+cls+'">'+val+'</div><div class="pb-lbl">'+lbl+'</div></div>'
}
function bsum(val, lbl, color) {
 return '<div class="bsum"><div class="bsum-num" style="color:'+color+'">'+val+'</div><div class="bsum-lbl">'+lbl+'</div></div>'
}


function updateStats(total, usedB) {
 document.getElementById('s-total').textContent = total
 document.getElementById('s-dup').textContent   = dupCount
 document.getElementById('s-used').textContent  = fmt(usedB)
 document.getElementById('s-saved').textContent = fmt(savedBytes)
 document.getElementById('sc-used').textContent = fmt(usedB)
}


function renderFiles(files) {
 var bc = {Document:'b-doc',Image:'b-img',Code:'b-code',Video:'b-img',Archive:'b-doc',Financial:'b-code'}
 var el = document.getElementById('file-list')
 if(!files||!files.length){ el.innerHTML='<div class="empty">No files yet.</div>'; return }
 el.innerHTML = files.map(function(f){
   return '<div class="file-item'+(f.seeded?' seed':'')+'">' +
     '<div class="file-left"><div class="file-name">'+f.filename+'</div>' +
     '<div class="file-hash">'+f.hash.slice(0,22)+'\u2026</div></div>' +
     '<div class="file-right">' +
       '<span class="badge '+(f.seeded?'b-seed':(bc[f.type]||'b-doc'))+'">'+(f.seeded?'pre-seeded':f.type)+'</span>' +
       (f.emp&&!f.seeded?'<span style="font-size:10px;color:var(--muted)">'+f.dept+'/'+f.emp+'</span>':'') +
       '<span class="file-size">'+fmt(f.size)+'</span>' +
     '</div></div>'
 }).join('')
}


async function resetSystem() {
 await fetch('/reset',{method:'POST'})
 dupCount=0; savedBytes=0
 document.getElementById('result-box').className='result'
 document.getElementById('s-dup').textContent='0'
 document.getElementById('s-saved').textContent='0 B'
 await loadInit()
}


function fmt(b) {
 if(b==null) return '\u2014'
 if(b<1024) return b+' B'
 if(b<1048576) return (b/1024).toFixed(1)+' KB'
 if(b<1073741824) return (b/1048576).toFixed(2)+' MB'
 return (b/1073741824).toFixed(2)+' GB'
}
</script>
</body>
</html>"""




# ─────────────────────────────────────────────────────────────────
# HTTP Handler
# ─────────────────────────────────────────────────────────────────
engine = SDDaaS()




class Handler(BaseHTTPRequestHandler):
   rbufsize = -1          # use OS default (typically 64 KB) — faster than line-by-line
   wbufsize = 1 << 16    # 64 KB write buffer
   timeout  = 120        # allow large uploads up to 2 min


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
       ct     = self.headers.get("Content-Type", "")
       length = int(self.headers.get("Content-Length", 0))
       body   = self.rfile.read(length)


       # ── Single file (detailed 3-step view) ────────────────
       if self.path == "/upload_single":
           parts = parse_multipart(ct, body)
           emp   = next((p["data"].decode() for p in parts if p["name"]=="emp"),  "")
           dept  = next((p["data"].decode() for p in parts if p["name"]=="dept"), "")
           ft    = next((p["data"].decode() for p in parts if p["name"]=="type"), "Document")
           fp    = next((p for p in parts if p["name"]=="file"), None)


           if not fp:
               self._json({"error": "no file"}); return


           file_data = fp["data"]
           filename  = fp["filename"]
           file_hash = hashlib.sha256(file_data).hexdigest()
           size      = len(file_data)


           result = engine.process_file(file_hash, filename, size, emp, ft, dept)
           result["filename"] = filename
           result["size"]     = size
           summ = engine.storage_summary()
           result["stored_unique"] = summ["stored_unique"]
           result["used_bytes"]    = summ["used_bytes"]
           result["store"]         = engine.get_store()
           self._json(result)


       # ── Batch JSON: browser pre-hashed, send hashes+metadata only ──
       elif self.path == "/upload_batch_json":
           payload = json.loads(body.decode("utf-8"))
           emp   = payload.get("emp", "")
           dept  = payload.get("dept", "")
           ft    = payload.get("type", "Document")
           files = payload.get("files", [])   # [{filename, hash, size}, ...]


           result = engine.process_batch_hashes(files, emp, ft, dept)
           summ   = engine.storage_summary()
           result["stored_unique"] = summ["stored_unique"]
           result["used_bytes"]    = summ["used_bytes"]
           result["store"]         = engine.get_store()
           self._json(result)


       # ── Batch (FormData with multiple files) — kept for compat ──
       elif self.path == "/upload_batch":
           parts  = parse_multipart(ct, body)
           emp    = next((p["data"].decode() for p in parts if p["name"]=="emp"),  "")
           dept   = next((p["data"].decode() for p in parts if p["name"]=="dept"), "")
           ft     = next((p["data"].decode() for p in parts if p["name"]=="type"), "Document")
           fparts = [p for p in parts if p["name"]=="files"]


           file_items = [{"filename": p["filename"], "data": p["data"]} for p in fparts]
           result = engine.process_batch_files(file_items, emp, ft, dept)


           summ = engine.storage_summary()
           result["stored_unique"] = summ["stored_unique"]
           result["used_bytes"]    = summ["used_bytes"]
           result["store"]         = engine.get_store()
           self._json(result)


       elif self.path == "/reset":
           engine.reset()
           engine.seed(os.path.join(os.path.dirname(os.path.abspath(__file__)), "dataset.csv"))
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
   CSV = os.path.join(SCRIPT_DIR, "dataset.csv")


   print("=" * 65)
   print("  SDDaaS v8 — Instant Batch (FormData + Server Hashing)")
   print(f"  Pre-seeding {TOTAL_PER_CLIENT:,} files/client "
         f"({len(SEEDED_FILES)} real + {N_RANDOM_HASHES:,} random hashes)...")
   engine.seed(CSV)
   total = engine.n_employees * TOTAL_PER_CLIENT
   print(f"  Scale: {engine.n_employees:,} clients x {TOTAL_PER_CLIENT:,} = {total:,} BF entries")
   print("=" * 65)


   PORT   = 8765
   server = ThreadingHTTPServer(("localhost", PORT), Handler)
   url    = f"http://localhost:{PORT}"
   print(f"  Open  ->  {url}")
   print("  Stop with Ctrl+C")
   print("=" * 65)


   threading.Timer(0.9, lambda: webbrowser.open(url)).start()
   try:
       server.serve_forever()
   except KeyboardInterrupt:
       print("\nServer stopped.")
