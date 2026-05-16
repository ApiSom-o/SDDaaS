import os

# สร้างโฟลเดอร์สำหรับเก็บไฟล์
folder_name = "Unique_Test_Dataset"
if not os.path.exists(folder_name):
    os.makedirs(folder_name)

print(f"กำลังเริ่มผลิตไฟล์ที่เป็น Unique 100% จำนวน 5,000 ไฟล์ลงในโฟลเดอร์ '{folder_name}'...")

# ลูปสร้างไฟล์ 5,000 ไฟล์ โดยให้เนื้อหาผันแปรตามค่า i (ไม่มีทางซ้ำกัน)
for i in range(1, 5001):
    file_path = os.path.join(folder_name, f"document_{i:04d}.pdf")
    with open(file_path, "wb") as f:
        # ใช้โครงสร้าง PDF พื้นฐาน และฝังค่า i ลงไปในเนื้อหา ทำให้ค่า Hash ของทุกไฟล์ต่างกันโดยสิ้นเชิง
        content = f"%PDF-1.4\n1 0 obj\n<< /Title (Unique Doc {i}) >>\nstream\nThis is a unique file identifier number: {i}\nendstream\nendobj\n%%EOF".encode()
        f.write(content)

print(f"เสร็จสมบูรณ์! '{folder_name}' พร้อมใช้งาน")
