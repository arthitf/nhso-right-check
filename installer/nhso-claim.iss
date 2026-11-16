; ตัวติดตั้ง Windows (Inno Setup 6) — พัฒนาโดย arthit.f
; build: iscc /DAppVersion=0.2.0 installer\nhso-claim.iss   (ต้อง build dist\nhso-claim.exe ก่อน)
#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6B1E3C2A-8F4D-4C55-9A61-3A7C0C1A1F08}
AppName=NHSO Claim Tool
AppVersion={#AppVersion}
AppPublisher=arthit.f
AppComments=ตรวจสอบสิทธิ (SRM) และปิดสิทธิ (NHSO ENDPOINT) — พัฒนาโดย arthit.f
; ติดตั้งรายผู้ใช้ ไม่ต้องสิทธิ Admin และเขียน .env / ประวัติในโฟลเดอร์โปรแกรมได้
PrivilegesRequired=lowest
DefaultDirName={localappdata}\NHSO Claim Tool
DefaultGroupName=NHSO Claim Tool
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=nhso-claim-setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=NHSO Claim Tool
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "สร้างไอคอนบน Desktop"; GroupDescription: "ไอคอน:"

[Files]
Source: "..\dist\nhso-claim.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\.env.example"; DestDir: "{app}"; Flags: ignoreversion
; สร้าง .env ครั้งแรกเท่านั้น — อัปเดตเวอร์ชันใหม่จะไม่ทับค่าที่ตั้งไว้
Source: "..\.env.example"; DestDir: "{app}"; DestName: ".env"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\index.html"; DestDir: "{app}"; DestName: "คู่มือ.html"; Flags: ignoreversion
Source: "..\examples\*"; DestDir: "{app}\examples"; Flags: ignoreversion

[Icons]
Name: "{group}\NHSO Claim Tool"; Filename: "{app}\nhso-claim.exe"; WorkingDir: "{app}"
Name: "{group}\ตั้งค่า (.env)"; Filename: "notepad.exe"; Parameters: """{app}\.env"""
Name: "{group}\คู่มือ"; Filename: "{app}\คู่มือ.html"
Name: "{group}\ถอนการติดตั้ง"; Filename: "{uninstallexe}"
Name: "{userdesktop}\NHSO Claim Tool"; Filename: "{app}\nhso-claim.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "notepad.exe"; Parameters: """{app}\.env"""; Description: "เปิดไฟล์ตั้งค่า (.env) เพื่อใส่ HCODE / TOKEN"; Flags: postinstall nowait skipifsilent
Filename: "{app}\nhso-claim.exe"; WorkingDir: "{app}"; Description: "เปิด NHSO Claim Tool"; Flags: postinstall nowait skipifsilent unchecked
