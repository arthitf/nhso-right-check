# ตรวจสอบสิทธิ สปสช. (nhso-right-check)

โปรแกรมตรวจสอบสิทธิและปิดสิทธิ สปสช. — **พัฒนาโดย arthit.f**

📖 คู่มือผู้ใช้ (หน้าเว็บ): [`docs/index.html`](docs/index.html) · 🛠 คู่มือผู้ดูแล (อัป GitHub / ออก Release): [`docs/DEPLOY.md`](docs/DEPLOY.md)

## หน้าจอ

```
run.bat            ← ดับเบิลคลิก/รันเปล่า = เปิดหน้าต่างโปรแกรม (pywebview) ถ้าไม่มี WebView จะเปิดในเบราว์เซอร์ (http://127.0.0.1:8765)
run.bat ui --browser   ← บังคับเปิดในเบราว์เซอร์
```

แท็บ: 01 ตรวจสอบสิทธิ · 02 เสียบบัตร (NHSO Secure SmartCard Agent: อ่านบัตร + รูปหน้าบัตร + ตรวจสิทธิ + ปิดสิทธิ) · 03 ตรวจแบบกลุ่ม (ลากไฟล์ Excel/CSV → สรุปโควตาก่อนตรวจ → บันทึกผลเป็น Excel) · 04 ปิดสิทธิ (ปุ่ม **กดปิดสิทธิ**) · 05 ยกเลิกปิดสิทธิ · 06 ประวัติ · 07 ตารางสิทธิ (vr_l_right) · 08 ตั้งค่า (SRM token / ค่าปิดสิทธิ ตั้งครั้งเดียว / เปิดแบบหน้าต่างหรือเบราว์เซอร์ `OPEN_MODE`)

## ดาวน์โหลดโปรแกรม (ไม่ต้องลง Python)

หน้า **Releases** ของ repo: `nhso-right-check-windows-setup.exe` (ตัวติดตั้ง) · `nhso-right-check-windows-x64.zip` (แตกไฟล์ใช้ได้เลย)

Windows มี 2 ไฟล์: `nhso-right-check.exe` (หน้าต่างโปรแกรม ไม่มีหน้าต่างดำ) และ `nhso-right-check-cli.exe` (คำสั่ง command line เช่น check-batch) · macOS เป็น `nhso-right-check.app` · `nhso-right-check-macos-arm64.zip` (Mac M1 ขึ้นไป) · `nhso-right-check-linux-x64.zip`

| ส่วน | API ที่ใช้ | เอกสารอ้างอิง |
|---|---|---|
| ตรวจสอบสิทธิ | **SRM** `GET /api/ucws/v1/right-search?pid=` | API Specification ตรวจสอบสิทธิ v1.5 (5 ส.ค. 2568) — ใช้แทน `ucws.nhso.go.th` ที่ **ยุติ 15 ม.ค. 2570** |
| ปิดสิทธิ / ยกเลิก | **NHSO ENDPOINT FOR SINGLE CLAIM** (แฟม 6–9) | DataSet v8.0 (07/12/2023) |

| เสียบบัตรยืนยันตัวตน + ปิดสิทธิ | **NHSO Secure SmartCard Agent** `localhost:8189` (`/api/smartcard/read`, `/api/nhso-service/save-endpoint`) | คู่มือ Secure SmartCard Agent v2.0 |

> โปรแกรมตัวอย่าง (เดโม) เพื่อเรียนรู้ API ของ สปสช. แต่ละเส้น — จากคู่มือ สปสช. สู่ตัวอย่างโปรแกรม · หน่วยบริการที่ HIS เชื่อมแล้วใช้ HIS ต่อได้ตามปกติ · ไม่ใช่โปรแกรมทางการของ สปสช.

## ขั้นตอนการทำงาน

```
เสียบบัตร → Login SRM Portal ─► token.txt
                                   │
 pid ─► [SRM right-search] ─► สิทธิหลัก (fundType=Y), เช็คเสียชีวิต
                                   │
            [NHSO ENDPOINT claim] ─► seq + authenCode ─► บันทึก SQLite
                                   │
            [cancel] ใช้ seq + transactionId จากประวัติ
```

## ติดตั้ง (Windows)

```bat
git clone https://github.com/arthitf/nhso-right-check.git
cd nhso-right-check
copy .env.example .env      &  REM แล้วแก้ค่า HCODE, NHSO_ENDPOINT_TOKEN, RECORDER_PID
run.bat --version           &  REM สร้าง .venv และติดตั้ง requests ให้อัตโนมัติ
```

ต้องมี Python 3.10 ขึ้นไป

## SRM token

โปรแกรมหา token ตามลำดับนี้:

1. `--token <access_token>` หรือ `SRM_ACCESS_TOKEN` ใน `.env`
2. **`%USERPROFILE%\SRM Smart Card Single Sign-On\token.txt`** — SRM จะเขียนไฟล์นี้ใหม่ทุกครั้งที่เสียบบัตรแล้ว Login SRM Portal โปรแกรมจะอ่านไฟล์นี้ให้เอง
3. token ที่วางเองด้วย `run.bat token set` (เก็บไว้ที่ `.srm_token.txt`)

ถ้าระบบตอบ 401 โปรแกรมจะอ่าน token.txt ใหม่ก่อน (กรณีที่เพิ่งเสียบบัตรใหม่) ถ้ายังไม่ได้ จะใช้ `refresh_token` ไปขอ token ใหม่ที่ `POST {SRM Portal}/api/scard/access-token`

```bat
run.bat token show     &  REM ดูว่าเจอ token ไหม และไฟล์อัปเดตล่าสุดเมื่อไร
run.bat token set      &  REM วาง token เอง
```

## การใช้งาน

```bat
REM ตรวจสอบสิทธิ
run.bat check 1234567890123
run.bat check 1234567890123 --raw
run.bat check-batch examples\check_input.csv -o check_result.csv

REM ปิดสิทธิ (ถ้าไม่ใส่ --send จะเป็น DRY-RUN คือแสดง JSON อย่างเดียว ยังไม่ส่ง)
run.bat close --pid 1234567890123 --service PG0060001 --privilege 700 --paid 0 --vn VN0001
run.bat close --pid 1234567890123 --service PG0060001 --privilege 700 --send

REM ปิดสิทธิแบบ batch
run.bat close-batch examples\close_input.csv -o close_result.csv
run.bat close-batch examples\close_input.csv --send

REM ยกเลิกการปิดสิทธิ (หา seq จากประวัติให้เอง)
run.bat cancel --trans 13814261116083000123 --send

REM ประวัติ
run.bat history -n 50

REM ค้นหาตารางรหัสสิทธิ
run.bat rights UCS
run.bat rights ข้าราชการ
```

ถ้าส่งไป `--env prod` โปรแกรมจะให้พิมพ์ `YES` ยืนยันก่อนทุกครั้ง (ข้ามได้ด้วย `--yes`)

### คอลัมน์ CSV สำหรับ close-batch

| คอลัมน์ | บังคับ | หมายเหตุ |
|---|---|---|
| pid | ✔ | 13 หลัก โปรแกรมตรวจ check digit ให้ |
| service_code | ✔ | claimServiceCode เช่น PG0060001 |
| privilege_amount | ✔ | ค่าบริการที่เบิกได้ |
| paid_amount | | ค่าบริการที่เบิกไม่ได้ (ค่าเริ่มต้น 0) |
| total_amount | | ถ้าไม่ใส่จะใช้ paid + privilege |
| service_datetime / invoice_datetime | | `YYYY-MM-DD HH:MM:SS` ปี ค.ศ. (ถ้าไม่ใส่จะใช้เวลาปัจจุบัน) ห้ามเป็นเวลาในอนาคต |
| visit_number, department_code, department_name | | |
| main_inscl | | ถ้าใส่จะข้ามการตรวจสอบสิทธิผ่าน SRM |
| transaction_id | | ถ้าไม่ใส่จะสร้างให้เป็น `HCODE + yyMMddHHmmss + สุ่ม 3 หลัก` |

ไฟล์ผลลัพธ์เป็น UTF-8 BOM เปิดใน Excel แล้วภาษาไทยไม่เพี้ยน

## ⚠ ก่อนใช้งานจริง

- **โควตา API:** การตรวจสอบสิทธิผ่าน API ใช้โควตาของหน่วยบริการ การตรวจจำนวนมาก/ซ้ำซ้อนอาจทำให้โควตาหมด — ตรวจแบบกลุ่มแนะนำ **https://srmportal.nhso.go.th/ → ตรวจสอบสิทธิแบบกลุ่ม** (โปรแกรมมี cache ตรวจซ้ำ 10 นาที, ตัดเลขบัตรซ้ำ, จำกัดจำนวน batch) ดู [SECURITY.md](SECURITY.md)

- **path ของ API ปิดสิทธิ**: เอกสาร v8.0 ระบุแค่ base URL ไม่ได้บอก path ค่าที่ตั้งไว้ (`api/nhso-claim-detail`, ยกเลิกใช้ `PUT`) ต้องเทียบกับ Swagger ก่อน ที่ https://nhsoapi.nhso.go.th/nhsoendpoint/swagger-ui.html (Production) แล้วแก้ใน `.env` ได้ (`NHSO_CLAIM_PATH`, `NHSO_CANCEL_PATH`, `NHSO_CANCEL_METHOD`)
- ค่าเริ่มต้นคือ **Production** (`NHSO_ENV=prod`) — token จาก SRM Portal ตัวจริงใช้กับ Test Zone ไม่ได้ ถ้ามีสิทธิ Test Zone เปลี่ยนเป็น `test` ได้ที่แท็บตั้งค่า
- TOKEN KEY ของ ENDPOINT ใช้ได้เฉพาะ HCODE ที่ยื่นขอเท่านั้น (ใช้แบบฟอร์มขอ TOKEN KEY ส่งไปที่ newauthen@nhso.go.th)
- repo เป็น **Public** ได้ (โค้ดเรียกเฉพาะ API เปิดของ สปสช.) แต่ **ห้าม commit** `.env`, token, `*.sqlite3`, ไฟล์ผลลัพธ์ CSV ที่มีข้อมูลผู้ป่วย — กันไว้ใน `.gitignore` แล้ว และ CI ตรวจซ้ำทุกครั้งที่ push
- เลขบัตรใน log และในหน้าจอจะถูกปิดบางส่วน (`1-xxxx-xxxxx-45-0`) แต่ในฐานข้อมูล SQLite จะเก็บเลขเต็ม เพราะต้องใช้ตอนยกเลิก

## Build โปรแกรมเอง

```bash
pip install -r requirements.txt pyinstaller
python build.py        # ได้ dist/nhso-right-check(.exe) ของ OS ที่รัน
```

หรือสร้าง tag `v*` บน GitHub แล้ว Actions (`release.yml`) จะ build ครบ 3 OS แล้วแนบเข้า Release ให้

## พัฒนา

```bash
pip install -e ".[dev]"
pytest -q
```

GitHub Actions รัน test บน Ubuntu และ Windows (Python 3.10 / 3.12) ทุกครั้งที่ push

## โครงสร้างโปรเจกต์

```
nhso_right_check/
  config.py      ตั้งค่า / .env / base URL test-prod
  srm.py         ตรวจสอบสิทธิ SRM + จัดการ token
  endpoint.py    ปิดสิทธิ / ยกเลิก NHSO ENDPOINT
  validation.py  ตรวจ pid, จำนวนเงิน, hcode
  rights.py      ตารางรหัสสิทธิ (data/vr_l_right.csv, vr_l_right_main.csv)
  store.py       ประวัติ SQLite
  batch.py       ตรวจแบบกลุ่มจากหน้าจอ (อ่าน xlsx/csv, งานเบื้องหลัง, เขียน xlsx)
  cli.py         คำสั่ง
  web.py         หน้าจอ (local web server, stdlib)
  static/index.html  หน้าจอ UI
docs/index.html  คู่มือหน้าเว็บ
build.py         build ไฟล์โปรแกรม (PyInstaller)
tests/           pytest (mock HTTP ไม่มีการยิง API จริง)
```
