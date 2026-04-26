import time
import hashlib
import random
import string
import math
import matplotlib.pyplot as plt

# ─────────────────────────────────────────────────────────────────
# Parameters
# ─────────────────────────────────────────────────────────────────
random.seed(42)

CLIENT_IDS    = ["ClientA", "ClientB", "ClientC"]
FILE_TYPES    = ["Document", "Image", "Code"]
N_SIZES       = [100, 500, 1000, 5000, 10000, 50000]
REPEATS       = 300      
DUPLICATE_RATIO  = 0.40  
AVG_FILE_SIZE_KB = 512   
N_PARTITIONS  = len(CLIENT_IDS) * len(FILE_TYPES)  

def random_hash():
    return hashlib.sha256(
        ''.join(random.choices(string.ascii_letters + string.digits, k=64)).encode()
    ).hexdigest()

class LinearSearch:
    def __init__(self):
        self.store = []
    def insert(self, h):
        self.store.append(h)
    def search(self, h):
        for item in self.store:
            if item == h:
                return True
        return False

class BloomFilter:
    def __init__(self, capacity, fpr=0.01):
        capacity      = max(capacity, 1)
        self.m        = math.ceil(-capacity * math.log(fpr) / (math.log(2) ** 2))
        self.k        = math.ceil((self.m / capacity) * math.log(2))
        self.bit_array = bytearray(self.m)
        self._n       = 0
    def _hashes(self, item):
        h1 = int(hashlib.md5(item.encode()).hexdigest(),  16)
        h2 = int(hashlib.sha1(item.encode()).hexdigest(), 16)
        return [(h1 + i * h2) % self.m for i in range(self.k)]
    def insert(self, item):
        for idx in self._hashes(item):
            self.bit_array[idx] = 1
        self._n += 1
    def search(self, item):
        return all(self.bit_array[idx] for idx in self._hashes(item))

class SDDaaS:
    def __init__(self, capacity_per_partition=500, fpr=0.01):
        self.capacity = capacity_per_partition
        self.fpr      = fpr
        self.tree     = {}
        self.metadata = {}
    def _get_or_create_bf(self, client_id, file_type):
        if client_id not in self.tree:
            self.tree[client_id] = {}
        if file_type not in self.tree[client_id]:
            self.tree[client_id][file_type] = BloomFilter(self.capacity, self.fpr)
        return self.tree[client_id][file_type]
    def insert(self, h, client_id, file_type):
        bf = self._get_or_create_bf(client_id, file_type)
        bf.insert(h)
        self.metadata[(client_id, file_type, h)] = True
    def search(self, h, client_id, file_type):
        if client_id not in self.tree or file_type not in self.tree[client_id]:
            return False
        if not self.tree[client_id][file_type].search(h):
            return False
        return (client_id, file_type, h) in self.metadata

def traditional_storage_mb(n):
    return (n * AVG_FILE_SIZE_KB) / 1024
def sddaas_storage_mb(n):
    n_unique    = int(n * (1 - DUPLICATE_RATIO))
    n_duplicate = n - n_unique
    return (n_unique * AVG_FILE_SIZE_KB + n_duplicate * 1) / 1024

# ─────────────────────────────────────────────────────────────────
# EXPERIMENT
# ─────────────────────────────────────────────────────────────────
search_results = []
for n in N_SIZES:
    cap = max(n // N_PARTITIONS, 1) + 100
    linear = LinearSearch()
    sddaas = SDDaaS(capacity_per_partition=cap)
    hashes = []
    for _ in range(n):
        h   = random_hash()
        cid = random.choice(CLIENT_IDS)
        ft  = random.choice(FILE_TYPES)
        linear.insert(h)
        sddaas.insert(h, cid, ft)
        hashes.append((h, cid, ft))

    queries  = random.choices(hashes, k=REPEATS // 2)
    queries += [(random_hash(), random.choice(CLIENT_IDS), random.choice(FILE_TYPES)) for _ in range(REPEATS // 2)]
    random.shuffle(queries)

    t0 = time.perf_counter()
    for h, _, _ in queries: linear.search(h)
    t_linear = (time.perf_counter() - t0) / REPEATS * 1e6

    t0 = time.perf_counter()
    for h, cid, ft in queries: sddaas.search(h, cid, ft)
    t_sddaas = (time.perf_counter() - t0) / REPEATS * 1e6

    search_results.append((n, t_linear, t_sddaas))
    print(f"n={n} | Linear: {t_linear:.2f}us | SDDaaS: {t_sddaas:.2f}us")

storage_results = []
for n in N_SIZES:
    storage_results.append((n, traditional_storage_mb(n), sddaas_storage_mb(n)))

# ─────────────────────────────────────────────────────────────────
# PLOTTING
# ─────────────────────────────────────────────────────────────────
ns = [r[0] for r in search_results]
t_linears = [r[1] for r in search_results]
t_sddaass = [r[2] for r in search_results]
s_trads = [r[1] for r in storage_results]
s_sdds = [r[2] for r in storage_results]

plt.figure(figsize=(12, 5))
plt.subplot(1, 2, 1)
plt.plot(ns, t_linears, 'o-', label='Linear O(n)')
plt.plot(ns, t_sddaass, 's-', label='SDDaaS O(1)')
plt.xscale('log')
plt.title('Search Time')
plt.legend()

plt.subplot(1, 2, 2)
plt.plot(ns, s_trads, 'o-', label='Traditional')
plt.plot(ns, s_sdds, 's-', label='SDDaaS')
plt.title('Storage Cost')
plt.legend()

plt.tight_layout()
plt.savefig('sddaas_comparison.png') # เซฟลงโฟลเดอร์ปัจจุบัน
print("\n[✓] บันทึกไฟล์กราฟ 'sddaas_comparison.png' เรียบร้อยแล้ว")
plt.show() # แสดงผลกราฟ