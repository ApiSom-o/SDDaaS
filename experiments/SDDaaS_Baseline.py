import time
import hashlib
import random
import string
import math
import matplotlib.pyplot as plt
from tabulate import tabulate




# ─────────────────────────────────────────────────────────────────
# Parameters
# ─────────────────────────────────────────────────────────────────
random.seed(42)  # ตั้ง seed เพื่อให้ผลการทดลองซ้ำได้ทุกครั้งที่รัน 

CLIENT_IDS    = ["ClientA", "ClientB", "ClientC"]     
FILE_TYPES    = ["Document", "Image", "Code"]         
N_SIZES       = [100, 500, 1000, 5000, 10000, 50000]  # จำนวน file records
REPEATS       = 300      # จำนวน query ต่อรอบ (เพื่อหาค่าเฉลี่ย latency ให้เสถียร ลด noise)
DUPLICATE_RATIO  = 0.40  # 40% ของไฟล์ที่ upload ซ้ำกัน 
AVG_FILE_SIZE_KB = 512   # ขนาดไฟล์เฉลี่ย 512 KB ต่อไฟล์ 
N_PARTITIONS  = len(CLIENT_IDS) * len(FILE_TYPES)  # จำนวน partition ทั้งหมด 




def random_hash():  # จำลอง SHA-256 hash ของ ไฟล์ จริงๆ ที่จะถูก deduplicate
    # สร้าง string สุ่มยาว 64 ตัวอักษร (a-z, A-Z, 0-9) แล้ว hash ด้วย SHA-256
    # ผลลัพธ์คือ hex string ยาว 64 ตัวอักษร แทน fingerprint ของไฟล์
    return hashlib.sha256(
        ''.join(random.choices(string.ascii_letters + string.digits, k=64)).encode()
    ).hexdigest()


  # ────────────────────────────────────────────────────────
    # Baseline (Traditional) srearch anf storage
  # ────────────────────────────────────────────────────────

class LinearSearch:
    def __init__(self):
        self.store = []  # list เก็บ hash ทั้งหมด ไม่แยก client / file type

    def insert(self, h):
        self.store.append(h)  # เพิ่ม hash เข้า list 

    def search(self, h):
        # วนลูปเปรียบเทียบ hash ทีละตัวตั้งแต่ต้น list จนจบ
        for item in self.store:
            if item == h:
                return True   # เจอ = ไฟล์ซ้ำ (duplicate)
        return False          # ไม่เจอจนครบ = ไฟล์ใหม่ (new file)

def traditional_storage_mb(n):
    # Traditional Storage: เก็บทุกไฟล์ทั้งซ้ำและไม่ซ้ำโดยไม่มี deduplication
    return (n * AVG_FILE_SIZE_KB) / 1024

 # ────────────────────────────────────────────────────────
    # Bloom Filterเคย insert ไม่เคย insert 
 # ────────────────────────────────────────────────────────

class BloomFilter:
    def __init__(self, capacity, fpr=0.01):
        capacity       = max(capacity, 1)  # ป้องกัน capacity = 0 ซึ่งทำให้ log(0) error
        self.m         = math.ceil(-capacity * math.log(fpr) / (math.log(2) ** 2))
        # m = จำนวน bits ใน bit array
        # ยิ่ง fpr ต่ำ หรือ capacity สูง = m ยิ่งใหญ่ (ใช้ memory มากขึ้น)
        self.k         = math.ceil((self.m / capacity) * math.log(2))
        # k = จำนวน hash functions ที่ใช้ต่อ item
        # k ที่เหมาะสมช่วย balance ระหว่าง false positive กับ computation cost
        self.bit_array = bytearray(self.m)  # bit array ขนาด m bits เริ่มต้นเป็น 0 ทั้งหมด (range 0-255 per byte)
        self._n        = 0  # ตัวนับจำนวน item ที่ insert จริงๆ

    def _hashes(self, item):
        # ใช้ h1 (MD5) และ h2 (SHA1) สร้าง k positions
        h1 = int(hashlib.md5(item.encode()).hexdigest(),  16)  # แปลง MD5 hex string to integer
        h2 = int(hashlib.sha1(item.encode()).hexdigest(), 16)  # แปลง SHA1 hex string to integer
        return [(h1 + i * h2) % self.m for i in range(self.k)]  # คืน list ของ k positions ใน bit array

    def insert(self, item):
        # set bit ที่ตำแหน่ง index ทุกตัวที่ _hashes คืนมาให้เป็น 1
        # ถ้ามี collision ก็ไม่เป็นไร เพราะ set 1 ซ้ำไม่มีผลเสีย
        for idx in self._hashes(item):
            self.bit_array[idx] = 1
        self._n += 1  # เพิ่มตัวนับ item

    def search(self, item):
        # ตรวจสอบ: ถ้าทุก bit ใน k positions เป็น 1 = อาจ เคย insert 
        #           ถ้ามี bit ใดสักตัวเป็น 0 = ไม่เคย insert แน่นอน 
        return all(self.bit_array[idx] for idx in self._hashes(item))


 # ────────────────────────────────────────────────────────────────────────
    # SDDaaS search and storage
 # ────────────────────────────────────────────────────────────────────────

class SDDaaS:
    def __init__(self, capacity_per_partition=500, fpr=0.01):
        self.capacity = capacity_per_partition  # จำนวน item สูงสุดที่คาดว่าจะ insert ต่อ partition
        self.fpr      = fpr                     # target False Positive Rate 
        self.tree     = {}   # search tree: {client_id: {file_type: BloomFilter}}
        self.metadata = {}   # exact match index: {(client_id, file_type, hash): True}
                             # เก็บ key tuple ไว้ยืนยัน exact match กัน false positive จาก BF

    def _get_or_create_bf(self, client_id, file_type):# สร้าง branch ใหม่ใน tree ถ้า client หรือ file type ยังไม่มี
        if client_id not in self.tree:
            self.tree[client_id] = {}  # สร้าง branch ระดับ client ใหม่ใน tree (layer 1)
        if file_type not in self.tree[client_id]:
            self.tree[client_id][file_type] = BloomFilter(self.capacity, self.fpr)
            # สร้าง BloomFilter ใหม่สำหรับ client นี้ + file type นี้โดยเฉพาะ (layer 2)
        return self.tree[client_id][file_type]  # คืน BF ที่ตรงกับ partition นั้น

    def insert(self, h, client_id, file_type):
        bf = self._get_or_create_bf(client_id, file_type)  # ดึงหรือสร้าง BF ของ partition ที่ถูกต้อง
        bf.insert(h)  # insert hash h ลงใน BloomFilter ของ partition นั้น
        self.metadata[(client_id, file_type, h)] = True
        # เก็บ exact key tuple ไว้ใน metadata dict สำหรับยืนยัน exact match ป้องกัน false positive

    def search(self, h, client_id, file_type):
        if client_id not in self.tree or file_type not in self.tree[client_id]:
            return False  # partition ยังไม่ถูกสร้าง = ไม่มีไฟล์ใน partition นี้แน่นอน = ไฟล์ใหม่
        if not self.tree[client_id][file_type].search(h):
            return False  # BF บอกว่าไม่มี = ไม่มีแน่นอน (no false negative) = ไฟล์ใหม่
        return (client_id, file_type, h) in self.metadata
        # BF บอกว่า "อาจมี" = ยืนยัน exact match ด้วย metadata
        # ถ้า key อยู่ใน metadata = duplicate จริง | ถ้าไม่อยู่ = false positive จาก BF


def sddaas_storage_mb(n):
    # SDDaaS Storage: ใช้ deduplication ลดพื้นที่จัดเก็บ
    n_unique    = int(n * (1 - DUPLICATE_RATIO))  # จำนวนไฟล์ unique ที่ต้องเก็บเนื้อหาจริง
    n_duplicate = n - n_unique                     # จำนวนไฟล์ซ้ำ เก็บแค่ reference pointer (ไม่เก็บเนื้อหาซ้ำ)
    return (n_unique * AVG_FILE_SIZE_KB + n_duplicate * (1 / 1024)) / 1024




# ─────────────────────────────────────────────────────────────────
# EXPERIMENT
# ─────────────────────────────────────────────────────────────────
search_results  = []  # เก็บผลลัพธ์ latency (n, t_linear, t_sddaas)
storage_results = []  # เก็บผลลัพธ์ storage (n, traditional_mb, sddaas_mb)


for n in N_SIZES:
    cap    = max(n // N_PARTITIONS, 1) + 100
    # capacity ต่อ partition = n/9 + buffer 100
    # buffer 100 เผื่อไว้กัน BF overflow จากความไม่สม่ำเสมอของการกระจาย random
    # ค่า cap นี้ใช้คำนวณ m และ k ของ BF แต่ละ partition
    linear = LinearSearch()
    sddaas = SDDaaS(capacity_per_partition=cap)
    hashes = []

    # สร้างข้อมูลจำลอง n ไฟล์ แต่ละไฟล์มี hash, client_id, file_type
    # จำลองพฤติกรรมระบบ: client อัปโหลดไฟล์หลากหลายประเภท
    for _ in range(n):
        h   = random_hash()                   # สร้าง hash จำลองแทน fingerprint ของไฟล์จริง
        cid = random.choice(CLIENT_IDS)       # สุ่มเลือก client ที่อัปโหลด
        ft  = random.choice(FILE_TYPES)       # สุ่มเลือกประเภทไฟล์
        linear.insert(h)                      # insert เข้า Linear Search 
        sddaas.insert(h, cid, ft)             # insert เข้า SDDaaS 
        hashes.append((h, cid, ft))           # เก็บไว้ใช้สร้าง query mix ภายหลัง

    # สร้าง query mix แบบ 50/50 เพื่อจำลอง:
    #   50% existing → ไฟล์ที่เคย insert แล้ว (ทดสอบ duplicate detection)
    #   50% new      → ไฟล์ใหม่ที่ไม่เคย insert (ทดสอบ new file detection)
    queries  = random.choices(hashes, k=REPEATS // 2)   # ดึงไฟล์ที่เคย insert มาเป็น query
    queries += [(random_hash(), random.choice(CLIENT_IDS),
                 random.choice(FILE_TYPES)) for _ in range(REPEATS // 2)]  # สร้างไฟล์ใหม่ที่ไม่เคย insert
    random.shuffle(queries)  # สลับลำดับ query 

    # วัดเวลา Linear Search: รัน REPEATS queries แล้วหารเฉลี่ย แปลงเป็น µs (×1e6)
    t0 = time.perf_counter()
    for h, _, _ in queries:
        linear.search(h)
    t_linear = (time.perf_counter() - t0) / REPEATS * 1e6

    # วัดเวลา SDDaaS: รัน REPEATS queries แล้วหารเฉลี่ย แปลงเป็น µs (×1e6)
    # SDDaaS ใช้ cid/ft เพื่อ route ไปยัง partition ที่ถูกต้องก่อน search BF
    t0 = time.perf_counter()
    for h, cid, ft in queries:
        sddaas.search(h, cid, ft)
    t_sddaas = (time.perf_counter() - t0) / REPEATS * 1e6

    search_results.append((n, t_linear, t_sddaas))
    storage_results.append((n, traditional_storage_mb(n), sddaas_storage_mb(n)))





# ─────────────────────────────────────────────────────────────────
# PRINT TABLE: Search Latency Results
# ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("TABLE I – Search Latency (µs)  [lower = better]")
print("=" * 60)
search_rows = [
    [f"{n:,}", f"{t_lin:.2f}", f"{t_sd:.2f}"]
    for n, t_lin, t_sd in search_results
]
print(tabulate(
    search_rows,
    headers=["N (files)", "Linear Search O(n)", "SDDaaS O(1)"],
    tablefmt="grid",
    colalign=("right", "right", "right")
))



# ─────────────────────────────────────────────────────────────────
# PRINT TABLE: Storage Cost Results
# ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("TABLE II – Storage Cost (MB)  [lower = better]")
print("=" * 60)
storage_rows = [
    [f"{n:,}", f"{t_stor:.2f}", f"{s_stor:.2f}",
     f"{((t_stor - s_stor) / t_stor * 100):.1f}%"]
    for n, t_stor, s_stor in storage_results
]
print(tabulate(
    storage_rows,
    headers=["N (files)", "Traditional (MB)", "SDDaaS (MB)", "Space Saved"],
    tablefmt="grid",
    colalign=("right", "right", "right", "right")
))
 
 

# ─────────────────────────────────────────────────────────────────
# PLOTTING
# ─────────────────────────────────────────────────────────────────
ns       = [r[0] for r in search_results]   # ดึงค่า n ทุก size มาเป็น x-axis
t_lins   = [r[1] for r in search_results]   # latency ของ Linear Search (µs)
t_sdds   = [r[2] for r in search_results]   # latency ของ SDDaaS (µs)
s_trads  = [r[1] for r in storage_results]  # storage ของ Traditional (MB)
s_sdds_  = [r[2] for r in storage_results]  # storage ของ SDDaaS (MB)


plt.figure(figsize=(12, 5))


# Search Time 
plt.subplot(1, 2, 1)
plt.plot(ns, t_lins,  'o-', label='Linear Search')  # Traditional = Linear Search baseline
plt.plot(ns, t_sdds,  's-', label='SDDaaS')        # SDDaaS = Partitioned BF + metadata
plt.xscale('log')
plt.xlabel('N (files)')
plt.ylabel('Latency (µs)')
plt.title('Search Time')
plt.legend()
plt.grid(True, linestyle='--', alpha=0.4)


# Storage Cost
plt.subplot(1, 2, 2)
plt.plot(ns, s_trads,  'o-', label='Traditional')  # Traditional เก็บทุกไฟล์รวมถึงซ้ำ
plt.plot(ns, s_sdds_,  's-', label='SDDaaS')        # SDDaaS เก็บเฉพาะ unique + reference
plt.xscale('log')
plt.xlabel('N (files)')
plt.ylabel('Storage (MB)')
plt.title('Storage Cost')
plt.legend()
plt.grid(True, linestyle='--', alpha=0.4)


plt.tight_layout()
plt.savefig('search_time_and_storage.png', dpi=300, bbox_inches='tight')  # บันทึกกราฟเป็น PNG ความละเอียดสูง

plt.show()
