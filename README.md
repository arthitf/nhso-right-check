# nhso-claim-tool

โปรแกรมตรวจสอบสิทธิและปิดสิทธิ สปสช. (Phase 1)

| ส่วน | API ที่ใช้ | เอกสารอ้างอิง |
|---|---|---|
| ตรวจสอบสิทธิ | **SRM** `GET /api/ucws/v1/right-search?pid=` | API Specification ตรวจสอบสิทธิ v1.5 (5 ส.ค. 2568) — ใช้แทน `ucws.nhso.go.th` ที่ **ยุติ 15 ม.ค. 2570** |
| ปิดสิทธิ / ยกเลิก | **NHSO ENDPOINT FOR SINGLE CLAIM** (แฟม 6–9) | DataSet v8.0 (07/12/2023) |

> Phase 2 (ไฟล์ถัดไป): เชื่อม NHSO Secure SmartCard Agent v2.0 (`localhost:8189`, `/api/nhso-service/save-endpoint`)

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
git clone https://github.com/<user>/nhso-claim-tool.git
cd nhso-claim-tool
copy .env.example .env      &  REM แล้วแก้ค่า HCODE, NHSO_ENDPOINT_TOKEN, RECORDER_PID
run.bat --version           &  REM สร้าง .venv และติดตั้ง requests ให้อัตโนมัติ
```

ต้องมี Python 3.9 ขึ้นไป

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

- **path ของ API ปิดสิทธิ**: เอกสาร v8.0 ระบุแค่ base URL ไม่ได้บอก path ค่าที่ตั้งไว้ (`api/nhso-claim-detail`, ยกเลิกใช้ `PUT`) ต้องเทียบกับ Swagger ก่อน ที่ https://test.nhso.go.th/nhsoendpoint/swagger-ui.html แล้วแก้ใน `.env` ได้ (`NHSO_CLAIM_PATH`, `NHSO_CANCEL_PATH`, `NHSO_CANCEL_METHOD`)
- ทดสอบใน **Test Zone** ให้ผ่านก่อนเปลี่ยนเป็น `NHSO_ENV=prod`
- TOKEN KEY ของ ENDPOINT ใช้ได้เฉพาะ HCODE ที่ยื่นขอเท่านั้น (ใช้แบบฟอร์มขอ TOKEN KEY ส่งไปที่ newauthen@nhso.go.th)
- **ห้าม commit** `.env`, token, `*.sqlite3`, ไฟล์ผลลัพธ์ CSV (มีอยู่ใน `.gitignore` แล้ว และ CI จะเช็กซ้ำ) ควรตั้ง repo เป็น **Private**
- เลขบัตรใน log และในหน้าจอจะถูกปิดบางส่วน (`1-xxxx-xxxxx-45-0`) แต่ในฐานข้อมูล SQLite จะเก็บเลขเต็ม เพราะต้องใช้ตอนยกเลิก

## พัฒนา

```bash
pip install -e ".[dev]"
pytest -q
```

GitHub Actions รัน test บน Ubuntu และ Windows (Python 3.9 / 3.12) ทุกครั้งที่ push

## โครงสร้างโปรเจกต์

```
nhso_claim/
  config.py      ตั้งค่า / .env / base URL test-prod
  srm.py         ตรวจสอบสิทธิ SRM + จัดการ token
  endpoint.py    ปิดสิทธิ / ยกเลิก NHSO ENDPOINT
  validation.py  ตรวจ pid, จำนวนเงิน, hcode
  store.py       ประวัติ SQLite
  cli.py         คำสั่ง
tests/           pytest (mock HTTP ไม่มีการยิง API จริง)
```
