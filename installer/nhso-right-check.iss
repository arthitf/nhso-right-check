; ตัวติดตั้ง Windows (Inno Setup 6) — พัฒนาโดย arthit.f
; build: iscc /DAppVersion=0.6.0 installer\nhso-right-check.iss   (ต้อง build dist\nhso-right-check.exe ก่อน)
#ifndef AppVersion
  #define AppVersion "0.6.0"
#endif

[Setup]
AppId={{6B1E3C2A-8F4D-4C55-9A61-3A7C0C1A1F08}
AppName=ตรวจสอบสิทธิ สปสช.
AppVersion={#AppVersion}
AppPublisher=arthit.f
AppComments=ตรวจสอบสิทธิ (SRM) และปิดสิทธิ (NHSO ENDPOINT) — พัฒนาโดย arthit.f
; ติดตั้งรายผู้ใช้ ไม่ต้องสิทธิ Admin และเขียน .env / ประวัติในโฟลเดอร์โปรแกรมได้
PrivilegesRequired=lowest
DefaultDirName={localappdata}\NHSO Right Check
DefaultGroupName=ตรวจสอบสิทธิ สปสช.
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=nhso-right-check-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=ตรวจสอบสิทธิ สปสช.
WizardStyle=modern
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\nhso-right-check.exe

[Tasks]
Name: "desktopicon"; Description: "สร้างไอคอนบน Desktop"; GroupDescription: "ไอคอน:"

[Files]
Source: "..\dist\nhso-right-check.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\nhso-right-check-cli.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion
; สร้าง .env ครั้งแรกเท่านั้น — อัปเดตเวอร์ชันใหม่จะไม่ทับค่าที่ตั้งไว้
Source: "..\.env.example"; DestDir: "{app}"; DestName: ".env"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\index.html"; DestDir: "{app}"; DestName: "คู่มือ.html"; Flags: ignoreversion
Source: "..\examples\*"; DestDir: "{app}\examples"; Flags: ignoreversion

[Icons]
Name: "{group}\ตรวจสอบสิทธิ สปสช."; Filename: "{app}\nhso-right-check.exe"; WorkingDir: "{app}"
Name: "{group}\ตั้งค่า (.env)"; Filename: "notepad.exe"; Parameters: """{app}\.env"""
Name: "{group}\คู่มือ"; Filename: "{app}\คู่มือ.html"
Name: "{group}\ถอนการติดตั้ง"; Filename: "{uninstallexe}"
Name: "{userdesktop}\ตรวจสอบสิทธิ สปสช."; Filename: "{app}\nhso-right-check.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\nhso-right-check.exe"; WorkingDir: "{app}"; Description: "เปิดโปรแกรม (ตั้งค่าที่แท็บ 07 ตั้งค่า)"; Flags: postinstall nowait skipifsilent
