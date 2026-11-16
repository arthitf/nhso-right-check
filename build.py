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
data = os.path.join(here, "nhso_right_check", "data")
icon = {"Windows": "icon.ico", "Darwin": "icon.icns"}.get(platform.system())
icon_args = ["--icon", os.path.join(here, "assets", icon)] if icon else []

PyInstaller.__main__.run([
    os.path.join(here, "launcher.py"),
    "--name", "nhso-right-check",
    "--onefile",
    "--console",                      # มีหน้าต่าง console ไว้ดูสถานะ / ปิด = ปิดโปรแกรม
    "--add-data", f"{static}{os.pathsep}nhso_right_check/static",
    "--add-data", f"{data}{os.pathsep}nhso_right_check/data",
    "--clean", "--noconfirm",
    *icon_args,
])
print(f"build OK: {platform.system()} {platform.machine()} -> dist/")
