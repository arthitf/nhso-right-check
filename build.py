"""สร้างไฟล์โปรแกรมไฟล์เดียวด้วย PyInstaller (รันบน OS ไหน ได้โปรแกรมของ OS นั้น)

    pip install pyinstaller requests
    python build.py

ผลลัพธ์อยู่ที่ dist/  (Windows: nhso-right-check.exe, macOS/Linux: nhso-right-check)
"""
import os
import platform

import PyInstaller.__main__

here = os.path.dirname(os.path.abspath(__file__))
static = os.path.join(here, "nhso_right_check", "static")

PyInstaller.__main__.run([
    os.path.join(here, "launcher.py"),
    "--name", "nhso-right-check",
    "--onefile",
    "--console",                      # มีหน้าต่าง console ไว้ดูสถานะ / ปิด = ปิดโปรแกรม
    "--add-data", f"{static}{os.pathsep}nhso_right_check/static",
    "--clean", "--noconfirm",
])
print(f"build สำเร็จ: {platform.system()} {platform.machine()} -> dist/")
