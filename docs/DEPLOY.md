# คู่มือผู้ดูแล: อัปโค้ดขึ้น GitHub และสร้างไฟล์ดาวน์โหลด

> เอกสารภายในสำหรับผู้พัฒนา/ผู้ดูแล (ไม่ได้อยู่ในคู่มือหน้าเว็บสาธารณะ `docs/index.html`)

## ก. สร้าง repo

1. ไปที่ https://github.com/new
2. ตั้งชื่อ `nhso-right-check` เลือก **Public** ไม่ต้องติ๊ก README
3. กด **Create repository**

## ข. อัปโหลดโค้ด

### วิธีที่ 1 — ใช้ git (แนะนำ)

ใช้ไฟล์ `nhso-right-check.zip` ซึ่งมีประวัติ git และโฟลเดอร์ `.github` มาครบแล้ว

```bat
cd nhso-right-check
git remote add origin https://github.com/arthitf/nhso-right-check.git
git push -u origin main
```

### วิธีที่ 2 — ผ่านหน้าเว็บ

1. ในหน้า repo กด **uploading an existing file**
2. ลากไฟล์และโฟลเดอร์ทั้งหมดขึ้นไป **ยกเว้น** `.env`, token, `*.sqlite3`
3. `.github` เป็นโฟลเดอร์ซ่อน: บน Windows ให้เปิด View → Hidden items ก่อน
   ถ้าลากไม่ขึ้น ให้กด Add file → Create new file แล้วพิมพ์ชื่อ `.github/workflows/ci.yml` จากนั้นวางเนื้อหาไฟล์ลงไป (ทำแบบเดียวกันกับ `release.yml`)
4. กด **Commit changes**

## ค. สร้างไฟล์โปรแกรม Windows / Mac / Linux อัตโนมัติ

1. ไปที่ **Releases → Draft a new release**
2. ตั้ง Tag เป็น `v0.4.0` แล้วกด **Publish release**
3. รอประมาณ 5–10 นาที (ดูความคืบหน้าได้ที่แท็บ **Actions**) เสร็จแล้วไฟล์เหล่านี้จะถูกแนบไว้ใน Release

| ไฟล์ | สำหรับ |
|---|---|
| `nhso-right-check-windows-setup.exe` | ตัวติดตั้ง Windows |
| `nhso-right-check-windows-x64.zip` | Windows แบบแตกไฟล์ใช้ได้เลย |
| `nhso-right-check-macos-arm64.zip` | Mac M1 ขึ้นไป |
| `nhso-right-check-linux-x64.zip` | Linux |

ปุ่มดาวน์โหลดในคู่มือหน้าเว็บจะลิงก์ไปที่ `releases/latest/download/...` ให้เองอัตโนมัติ

## ง. เผยแพร่คู่มือหน้าเว็บ

- นำไฟล์ `docs/index.html` ไปวางบนเว็บ (เช่น pop08 → /links) **ก่อนวางให้แก้ค่า `GITHUB_REPO`** ท้ายไฟล์ให้เป็นชื่อ repo จริง
- หรือเปิด GitHub Pages: Settings → Pages → Branch `main` / folder `/docs` → Save

## ข้อห้าม

repo เป็น Public ได้ แต่ **ห้ามอัป** `.env`, token, ไฟล์ประวัติ `.sqlite3` และ CSV ที่มีข้อมูลผู้ป่วย
ไฟล์ `.gitignore` กันไว้ให้แล้ว และ CI จะตรวจซ้ำทุกครั้งที่ push
