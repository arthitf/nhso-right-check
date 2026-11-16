"""สร้างไฟล์โปรแกรมด้วย PyInstaller (รันบน OS ไหน ได้โปรแกรมของ OS นั้น)

    pip install pyinstaller requests pywebview
    python build.py

ผลลัพธ์อยู่ที่ dist/
  Windows: nhso-right-check.exe      (หน้าต่างโปรแกรม ไม่มีหน้าต่างดำ)
           nhso-right-check-cli.exe  (สำหรับคำสั่ง command line เช่น check-batch)
  macOS:   nhso-right-check.app
  Linux:   nhso-right-check
"""
import os
import platform

import PyInstaller.__main__

here = os.path.dirname(os.path.abspath(__file__))
system = platform.system()
static = os.path.join(here, "nhso_right_check", "static")
data = os.path.join(here, "nhso_right_check", "data")
icon = {"Windows": "icon.ico", "Darwin": "icon.icns"}.get(system)

common = [
    "--add-data", f"{static}{os.pathsep}nhso_right_check/static",
    "--add-data", f"{data}{os.pathsep}nhso_right_check/data",
    "--noconfirm",
] + (["--icon", os.path.join(here, "assets", icon)] if icon else [])


def build(name: str, *extra: str) -> None:
    PyInstaller.__main__.run([os.path.join(here, "launcher.py"), "--name", name, *extra, *common])


if system == "Windows":
    build("nhso-right-check", "--onefile", "--windowed", "--clean")
    build("nhso-right-check-cli", "--onefile", "--console")
elif system == "Darwin":
    build("nhso-right-check", "--windowed", "--onedir", "--clean",
          "--osx-bundle-identifier", "th.arthitf.nhso-right-check")
else:
    build("nhso-right-check", "--onefile", "--console", "--clean")
print(f"build OK: {system} {platform.machine()} -> dist/")
