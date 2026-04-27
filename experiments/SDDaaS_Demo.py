import json
import math
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib


# ═══════════════════════════════════════════════════════════════════════
# SECTION 1 — Core SDDaaS Engine
# ═══════════════════════════════════════════════════════════════════════

class BloomFilter:

    def __init__(self, capacity=500, fpr=0.01):
        capacity = max(capacity, 1)
        self.m = math.ceil(-capacity * math.log(fpr) / (math.log(2) ** 2))
        # m = จำนวน bits ใน bit array สูตรStandard 
        self.k = math.ceil((self.m / capacity) * math.log(2))
        # k = จำนวน hash functions สูตรStandard 
        self.bits = bytearray(self.m)  # bit array ขนาด m bits เริ่มเป็น 0 ทั้งหมด
        self.count = 0  # ตัวนับจำนวน item ที่ insert จริงๆ

    def _hashes(self, item: str):
        # double hashing: h(i) = (h1 + i*h2) mod m สร้าง k positions
        h1 = int(hashlib.md5(item.encode()).hexdigest(), 16)  # MD5 → integer
        h2 = int(hashlib.sha1(item.encode()).hexdigest(), 16)  # SHA1 → integer
        return [(h1 + i * h2) % self.m for i in range(self.k)]  # คืน k positions

    def insert(self, item: str):
        # set bit ทุก position ที่ _hashes คืนมาให้เป็น 1
        for idx in self._hashes(item):
            self.bits[idx] = 1
        self.count += 1

    def query(self, item: str):
        # คืนผล all-bits-1, list ค่า bit แต่ละ position
        # ใช้for show step-by-step visualization ใน frontend
        indices  = self._hashes(item)
        bit_vals = [self.bits[i] for i in indices]
        return all(bit_vals), bit_vals

    def stats(self):
        # คืน metadata ของ BF partition นี้ สำหรับแสดงผลใน frontend
        return {
            "m": self.m,
            "k": self.k,
            "count": self.count,
            "bits_set": sum(self.bits),
        }


# ───────────────────────────────────────────────────────────────────────

class SDDaaS:

    def __init__(self):
        self.tree = {}  # { client_id: { file_type: BloomFilter } }
        self.metadata = {}  # { (client_id, file_type, hash): True }
        self.stored = []  # list of stored file records

    def _get_or_create_bf(self, client_id, file_type):
        # สร้าง branch ใน tree ถ้ายังไม่มี client_id หรือ file_type นั้น
        self.tree.setdefault(client_id, {})
        if file_type not in self.tree[client_id]:
            self.tree[client_id][file_type] = BloomFilter(
                capacity=200, fpr=0.01
            )
        return self.tree[client_id][file_type]

    def process_file(self, file_hash, filename, size, client_id, file_type):
       
        #Main deduplication pipeline — คืน full step-by-step trace สำหรับ visualize ใน frontend
       
        steps = []

        # ── Step 1: Search Tree Routing ───────────────────────────
        # O(1) routing ด้วย 2 dict lookups (client_id → file_type)
        steps.append(
            {
                "step": 1,
                "title": "Search Tree Routing",
                "detail": f"Root  ->  {client_id}  ->  {file_type}",
                "note": (
                    f"O(1) routing: query is confined directly to the "
                    f"'{client_id}/{file_type}' partition — "
                    f"no full index scan required."
                ),
                "status": "info",
            }
        )

        # ── Step 2: Bloom Filter probabilistic check ──────────────
        # BF ตรวจสอบเบื้องต้นก่อน ถ้า BF บอก miss → ไม่มีแน่นอน (no false negative)
        bf = self._get_or_create_bf(client_id, file_type)
        bf_hit, bit_vals = bf.query(file_hash)
        stats = bf.stats()

        steps.append(
            {
                "step": 2,
                "title": "Bloom Filter — Probabilistic Check",
                "detail": (
                    f"Checking {stats['k']} bit positions via double hashing  |  "
                    f"bit array size: {stats['m']} bits  |  "
                    f"items in this partition: {stats['count']}"
                ),
                "bits": bit_vals[:8],  # แสดงแค่ 8 bits แรกใน UI
                "bf_all_one": bf_hit,
                "note": (
                    "All bits = 1  ->  'Probable Duplicate' — must verify with Metadata Index."
                    if bf_hit
                    else "Found a 0-bit  ->  file is definitely new "
                    "(Bloom Filters have no false negatives)."
                ),
                "status": "warn" if bf_hit else "new",
            }
        )

        if not bf_hit:
            # BF บอก miss → ไฟล์ใหม่แน่นอน → เก็บไฟล์
            bf.insert(file_hash)
            self.metadata[(client_id, file_type, file_hash)] = True
            self.stored.append(
                {
                    "hash": file_hash,
                    "filename": filename,
                    "size": size,
                    "client": client_id,
                    "type": file_type,
                }
            )
            steps.append(
                {
                    "step": 3,
                    "title": "File Stored",
                    "detail": f"SHA-256: {file_hash}",
                    "note": (
                        "Hash inserted into Bloom Filter and Metadata Index. "
                        "Ciphertext saved to Cloud Storage. Done."
                    ),
                    "status": "new",
                }
            )
            return {
                "duplicate": False,
                "steps": steps,
                "hash": file_hash,
                "stored_count": len(self.stored),
            }

        # ── Step 3: Metadata Index exact-match lookup ─────────────
        # BF บอก hit → ต้องยืนยัน exact match เพื่อกัน false positive
        key = (client_id, file_type, file_hash)
        exact_match = key in self.metadata

        steps.append(
            {
                "step": 3,
                "title": "Metadata Index — Exact Match Verification",
                "detail": f"Lookup tuple  ({client_id},  {file_type},  {file_hash[:16]}...)",
                "note": (
                    "Exact match found  ->  confirmed duplicate. "
                    "File will NOT be stored again."
                    if exact_match
                    else "No tuple found  ->  Bloom Filter false positive eliminated. "
                    "Treating file as new."
                ),
                "status": "dup" if exact_match else "new",
            }
        )

        if not exact_match:
            # BF false positive → เก็บเป็นไฟล์ใหม่
            bf.insert(file_hash)
            self.metadata[key] = True
            self.stored.append(
                {
                    "hash": file_hash,
                    "filename": filename,
                    "size": size,
                    "client": client_id,
                    "type": file_type,
                }
            )

        return {
            "duplicate": exact_match,
            "false_positive": bf_hit and not exact_match,
            "steps": steps,
            "hash": file_hash,
            "stored_count": len(self.stored),
        }

    def get_store(self):
        return list(reversed(self.stored))  # คืนไฟล์ล่าสุดก่อน

    def reset(self):
        # ล้างทุกอย่าง: tree, metadata, stored files
        self.tree.clear()
        self.metadata.clear()
        self.stored.clear()


# ═══════════════════════════════════════════════════════════════════════
# SECTION 2 — HTML Frontend (Single-page app ฝังใน Python string)
# ═══════════════════════════════════════════════════════════════════════

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SDDaaS Demo</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
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
  font-family: 'IBM Plex Sans', sans-serif;
  min-height: 100vh;
  padding: 2rem;
}
.layout {
  display: grid;
  grid-template-columns: 1fr 380px;
  gap: 1.5rem;
  max-width: 1200px;
  margin: 0 auto;
}
@media (max-width: 900px) {
  .layout {
    grid-template-columns: 1fr;
  }
}
.header {
  max-width: 1200px;
  margin: 0 auto 2rem;
  display: flex;
  align-items: center;
  gap: 1rem;
}
.logo {
  width: 40px;
  height: 40px;
  background: var(--accent);
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: 'IBM Plex Mono', monospace;
  font-weight: 500;
  color: #000;
  font-size: 14px;
  flex-shrink: 0;
}
.header-title h1 {
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.3px;
}
.header-title p {
  font-size: 13px;
  color: var(--muted);
  margin-top: 2px;
}
.panel {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.25rem;
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
  display: block;
}
.row2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 12px;
}
.field label {
  font-size: 12px;
  color: var(--muted);
  display: block;
  margin-bottom: 5px;
  letter-spacing: 0.5px;
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
.dropzone {
  border: 1.5px dashed var(--border2);
  border-radius: 8px;
  padding: 2rem 1rem;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  position: relative;
  margin-bottom: 12px;
}
.dropzone:hover,
.dropzone.over {
  border-color: var(--accent);
  background: rgba(0, 229, 160, 0.04);
}
.dropzone input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
  width: 100%;
  height: 100%;
}
.dropzone-icon {
  font-size: 2rem;
  margin-bottom: 8px;
}
.dropzone p {
  font-size: 13px;
  color: var(--muted);
}
.dropzone .selected {
  color: var(--accent);
  font-size: 14px;
  font-weight: 500;
}
.btn {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 20px;
  font-size: 14px;
  font-weight: 500;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.15s;
  border: none;
  font-family: inherit;
}
.btn-primary {
  background: var(--accent);
  color: #000;
}
.btn-primary:hover {
  opacity: 0.88;
}
.btn-primary:disabled {
  opacity: 0.35;
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
.result {
  border-radius: 10px;
  padding: 1rem 1.25rem;
  margin-top: 1rem;
  display: none;
  border: 1px solid;
  animation: fadeIn 0.25s ease;
}
.result.show {
  display: block;
}
.result.dup {
  background: rgba(255, 77, 77, 0.07);
  border-color: rgba(255, 77, 77, 0.3);
}
.result.new {
  background: rgba(0, 229, 160, 0.07);
  border-color: rgba(0, 229, 160, 0.3);
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
.result-sub {
  font-size: 12px;
  color: var(--muted);
}
.hash-display {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 10px;
  color: var(--code);
  background: rgba(168, 196, 255, 0.06);
  border: 1px solid rgba(168, 196, 255, 0.15);
  border-radius: 5px;
  padding: 6px 10px;
  word-break: break-all;
  margin-top: 8px;
}
.steps {
  margin-top: 14px;
  display: flex;
  flex-direction: column;
}
.step {
  display: flex;
  gap: 10px;
  padding: 10px 0;
  border-bottom: 1px solid var(--border);
  position: relative;
}
.step:last-child {
  border-bottom: none;
}
.step-connector {
  position: absolute;
  left: 14px;
  top: 28px;
  bottom: -10px;
  width: 1px;
  background: var(--border2);
  display: none;
}
.step:not(:last-child) .step-connector {
  display: block;
}
.step-num {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  flex-shrink: 0;
  z-index: 1;
  font-family: 'IBM Plex Mono', monospace;
}
.s-info .step-num {
  background: rgba(0, 153, 255, 0.15);
  color: var(--accent2);
  border: 1px solid rgba(0, 153, 255, 0.3);
}
.s-new .step-num {
  background: rgba(0, 229, 160, 0.15);
  color: var(--accent);
  border: 1px solid rgba(0, 229, 160, 0.3);
}
.s-warn .step-num {
  background: rgba(255, 184, 0, 0.12);
  color: var(--warn);
  border: 1px solid rgba(255, 184, 0, 0.3);
}
.s-dup .step-num {
  background: rgba(255, 77, 77, 0.12);
  color: var(--danger);
  border: 1px solid rgba(255, 77, 77, 0.3);
}
.step-body {
  flex: 1;
  min-width: 0;
}
.step-title {
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 2px;
}
.s-info .step-title {
  color: var(--accent2);
}
.s-new .step-title {
  color: var(--accent);
}
.s-warn .step-title {
  color: var(--warn);
}
.s-dup .step-title {
  color: var(--danger);
}
.step-detail {
  font-size: 11px;
  color: var(--muted);
  margin-bottom: 4px;
  font-family: 'IBM Plex Mono', monospace;
  word-break: break-all;
}
.step-note {
  font-size: 12px;
  color: var(--text);
  line-height: 1.5;
}
.bits {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  margin-top: 6px;
  align-items: center;
}
.bits-label {
  font-size: 11px;
  color: var(--muted);
}
.bit {
  width: 22px;
  height: 22px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  font-family: 'IBM Plex Mono', monospace;
}
.bit-1 {
  background: rgba(255, 77, 77, 0.15);
  color: var(--danger);
  border: 1px solid rgba(255, 77, 77, 0.3);
}
.bit-0 {
  background: rgba(0, 229, 160, 0.1);
  color: var(--accent);
  border: 1px solid rgba(0, 229, 160, 0.2);
}
.stat-row {
  display: flex;
  gap: 8px;
  margin-bottom: 1rem;
}
.stat {
  flex: 1;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  text-align: center;
}
.stat-num {
  font-size: 20px;
  font-weight: 600;
  font-family: 'IBM Plex Mono', monospace;
  color: var(--accent);
}
.stat-label {
  font-size: 10px;
  color: var(--muted);
  margin-top: 2px;
  letter-spacing: 0.5px;
  text-transform: uppercase;
}
.file-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  border-radius: 6px;
  margin-bottom: 6px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid var(--border);
  font-size: 12px;
  transition: background 0.15s;
}
.file-item:hover {
  background: rgba(255, 255, 255, 0.055);
}
.file-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.file-name {
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 180px;
}
.file-hash {
  font-family: 'IBM Plex Mono', monospace;
  color: var(--code);
  font-size: 10px;
}
.file-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 3px;
  flex-shrink: 0;
}
.badge {
  font-size: 10px;
  padding: 2px 7px;
  border-radius: 4px;
  font-weight: 500;
}
.b-doc {
  background: rgba(0, 153, 255, 0.15);
  color: #6ab0ff;
}
.b-img {
  background: rgba(0, 229, 160, 0.1);
  color: var(--accent);
}
.b-code {
  background: rgba(255, 184, 0, 0.12);
  color: var(--warn);
}
.b-client {
  font-size: 10px;
  color: var(--muted);
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

<div class="header">
  <div class="logo">SD</div>
  <div class="header-title">
    <h1>SDDaaS Live Demo</h1>
    <p>Secure Data Deduplication-as-a-Service — upload real files to test the deduplication pipeline</p>
  </div>
</div>

<div class="layout">

  <div>
    <div class="panel" style="margin-bottom:1.25rem">
      <div class="panel-title">Upload File</div>
      <div class="row2">
        <div class="field">
          <label>CLIENT ID</label>
          <select id="sel-client">
            <option>ClientA</option>
            <option>ClientB</option>
            <option>ClientC</option>
          </select>
        </div>
        <div class="field">
          <label>FILE TYPE</label>
          <select id="sel-type">
            <option>Document</option>
            <option>Image</option>
            <option>Code</option>
          </select>
        </div>
      </div>
      <div class="dropzone" id="dropzone">
        <input type="file" id="file-input">
        <div class="dropzone-icon">📂</div>
        <p id="dz-text">Click or drag a file here</p>
      </div>
      <div class="btn-row">
        <button class="btn btn-ghost" onclick="resetSystem()">&#8635; Reset System</button>
        <button
          class="btn btn-primary"
          id="btn-upload"
          onclick="uploadFile()"
          disabled
          style="padding: 10px 28px"
        >
          Upload &amp; Check &rarr;
        </button>
      </div>
    </div>

    <div class="result" id="result-box">
      <div class="result-head" id="result-head"></div>
      <div class="result-sub" id="result-sub"></div>
      <div class="hash-display" id="result-hash"></div>
      <div class="steps" id="steps"></div>
    </div>
  </div>

  <div>
    <div class="panel">
      <div class="panel-title">Stored Files</div>
      <div class="stat-row">
        <div class="stat">
          <div class="stat-num" id="s-total">0</div>
          <div class="stat-label">Stored</div>
        </div>
        <div class="stat">
          <div class="stat-num" id="s-dup" style="color: var(--danger)">0</div>
          <div class="stat-label">Duplicates</div>
        </div>
        <div class="stat">
          <div class="stat-num" id="s-saved" style="color: var(--warn)">0 B</div>
          <div class="stat-label">Space Saved</div>
        </div>
      </div>
      <div id="file-list"><div class="empty">No files stored yet.</div></div>
    </div>
  </div>

</div>

<script>
let selectedFile = null
let dupCount = 0
let savedBytes = 0
const dz = document.getElementById('dropzone')
const fi = document.getElementById('file-input')
const btn = document.getElementById('btn-upload')

fi.addEventListener('change', (e) => {
  if (e.target.files[0]) selectFile(e.target.files[0])
})
dz.addEventListener('dragover', (e) => {
  e.preventDefault()
  dz.classList.add('over')
})
dz.addEventListener('dragleave', () => {
  dz.classList.remove('over')
})
dz.addEventListener('drop', (e) => {
  e.preventDefault()
  dz.classList.remove('over')
  if (e.dataTransfer.files[0]) selectFile(e.dataTransfer.files[0])
})
function selectFile(f) {
  selectedFile = f
  const sizeStr = fmtSize(f.size)
  const displayText = `✓ ${f.name} (${sizeStr})`
  document.getElementById('dz-text').innerHTML =
    `<span class="selected">${displayText}</span>`
  btn.disabled = false
}
async function sha256hex(buf) {
  const h = await crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(h))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}
function fmtSize(b) {
  if (b < 1024) return b + ' B'
  if (b < 1048576) return (b / 1024).toFixed(1) + ' KB'
  return (b / 1048576).toFixed(2) + ' MB'
}
async function uploadFile() {
  if (!selectedFile) return
  btn.disabled = true

  const buf = await selectedFile.arrayBuffer()
  const hash = await sha256hex(buf)
  const cid = document.getElementById('sel-client').value
  const ft = document.getElementById('sel-type').value

  const res = await fetch('/process', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      hash,
      filename: selectedFile.name,
      size: selectedFile.size,
      client: cid,
      type: ft,
    }),
  })

  const data = await res.json()
  showResult(data, selectedFile.name, selectedFile.size)

  document.getElementById('dz-text').textContent = 'Click or drag a file here'
  fi.value = ''
  selectedFile = null
  btn.disabled = true
}
function showResult(data, fname, size) {
  const rb = document.getElementById('result-box')
  rb.className = 'result show ' + (data.duplicate ? 'dup' : 'new')

  document.getElementById('result-head').textContent = data.duplicate
    ? '🔴 Duplicate Detected — file was NOT stored again'
    : '🟢 New File Stored — file saved successfully'

  document.getElementById('result-sub').textContent = data.duplicate
    ? `"${fname}" already exists in this partition — only a lightweight reference is recorded`
    : `"${fname}" has been inserted into the Bloom Filter and Metadata Index`

  document.getElementById('result-hash').textContent = 'SHA-256:  ' + data.hash

  const sc = { info: 's-info', new: 's-new', warn: 's-warn', dup: 's-dup' }
  document.getElementById('steps').innerHTML = data.steps
    .map((s) => {
      const bitsHtml = s.bits
        ? (
            '<div class="bits">' +
            '<span class="bits-label">Bloom bits checked:</span>' +
            s.bits.map((b) => '<div class="bit ' + (b ? 'bit-1' : 'bit-0') + '">' + b + '</div>').join('') +
            '<span class="bits-label">&nbsp;' +
            (s.bf_all_one ? '&rarr; all 1s &rarr; probable duplicate' : '&rarr; 0 found &rarr; definitely new') +
            '</span>' +
            '</div>'
          )
        : ''

      return (
        '<div class="step ' + (sc[s.status] || 's-info') + '">' +
        '<div class="step-connector"></div>' +
        '<div class="step-num">' + s.step + '</div>' +
        '<div class="step-body">' +
        '<div class="step-title">' + s.title + '</div>' +
        '<div class="step-detail">' + s.detail + '</div>' +
        bitsHtml +
        '<div class="step-note">' + s.note + '</div>' +
        '</div>' +
        '</div>'
      )
    })
    .join('')

  if (data.duplicate) {
    dupCount++
    savedBytes += size
  }

  document.getElementById('s-total').textContent = data.stored_count
  document.getElementById('s-dup').textContent = dupCount
  document.getElementById('s-saved').textContent = fmtSize(savedBytes)
  renderFiles(data.store)
}
function renderFiles(files) {
  const bc = { Document: 'b-doc', Image: 'b-img', Code: 'b-code' }
  const el = document.getElementById('file-list')

  if (!files || !files.length) {
    el.innerHTML = '<div class="empty">No files stored yet.</div>'
    return
  }

  el.innerHTML = files
    .map(
      (f) =>
        '<div class="file-item">' +
        '<div class="file-left">' +
        '<div class="file-name">' + f.filename + '</div>' +
        '<div class="file-hash">' + f.hash.slice(0, 24) + '…</div>' +
        '</div>' +
        '<div class="file-right">' +
        '<span class="badge ' + (bc[f.type] || 'b-doc') + '">' + f.type + '</span>' +
        '<span class="b-client">' + f.client + '</span>' +
        '<span class="file-size">' + fmtSize(f.size) + '</span>' +
        '</div>' +
        '</div>'
    )
    .join('')
}
async function resetSystem() {
  await fetch('/reset', { method: 'POST' })
  dupCount = 0
  savedBytes = 0

  document.getElementById('result-box').className = 'result'
  document.getElementById('s-total').textContent = '0'
  document.getElementById('s-dup').textContent = '0'
  document.getElementById('s-saved').textContent = '0 B'
  document.getElementById('file-list').innerHTML =
    '<div class="empty">No files stored yet.</div>'
}
</script>
</body>
</html>"""


# ═══════════════════════════════════════════════════════════════════════
# SECTION 3 — HTTP Request Handler
# ═══════════════════════════════════════════════════════════════════════

engine = SDDaaS()  # สร้าง SDDaaS instance เดียว ใช้ร่วมกันทั้ง server


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [{args[1]}] {args[0]}")


    def do_GET(self):
        # GET / → ส่ง HTML frontend กลับไปให้ browser
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode())

    def do_POST(self):
        # POST /process → รัน deduplication pipeline
        # POST /reset   → ล้างระบบทั้งหมด
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))

        if self.path == "/process":
            result = engine.process_file(
                file_hash=body["hash"],
                filename=body["filename"],
                size=body["size"],
                client_id=body["client"],
                file_type=body["type"],
            )
            result["store"] = engine.get_store()  # แนบ list ไฟล์ทั้งหมดไปด้วย
            self._json(result)

        elif self.path == "/reset":
            engine.reset()
            self._json({"ok": True})

    def _json(self, data):
        # helper: ส่ง JSON response กลับไปให้ client
        payload = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(payload))
        self.end_headers()
        self.wfile.write(payload)



# ═══════════════════════════════════════════════════════════════════════
# SECTION 4 — Entrypoint
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    PORT = 8765
    server = HTTPServer(("localhost", PORT), Handler)
    url = f"http://localhost:{PORT}"

    print("=" * 55)
    print("  SDDaaS Demo Server")
    print(f"  Listening at  ->  {url}")
    print("  Stop with Ctrl+C")
    print("=" * 55)

    # เปิด browser อัตโนมัติหลัง
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")