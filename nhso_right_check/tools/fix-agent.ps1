<#
  ตรวจ / ซ่อม NHSO Secure SmartCard Agent — อัปเดต JRE 8 (Eclipse Temurin)
  ทำตามแนวทาง สปสช. (11 พ.ย. 2569): Stop service -> jre เดิมเป็น jre_old -> วาง jre ใหม่ -> Start service
  ไม่ใช่เครื่องมือทางการของ สปสช. · ส่วนหนึ่งของ nhso-right-check (พัฒนาโดย arthit.f)

  ใช้:  คลิกขวา fix-agent.bat -> Run as administrator
        powershell -ExecutionPolicy Bypass -File fix-agent.ps1 [-Mode check|fix|rollback] [-AgentDir C:\...] [-ZipPath C:\...\OpenJDK8U-jre_x64_windows_....zip]
#>
param(
  [ValidateSet('check', 'fix', 'rollback')][string]$Mode = 'fix',
  [string]$AgentDir = '',
  [string]$ZipPath = '',
  [string]$ResultFile = '',
  [switch]$NoPause
)
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
$Service = 'NHSOSecureSmartCardAgent'
$JreUrl = 'https://api.adoptium.net/v3/binary/latest/8/ga/windows/x64/jre/hotspot/normal/eclipse'
$Work = Join-Path $env:ProgramData 'nhso-right-check'
New-Item -ItemType Directory -Force -Path $Work | Out-Null
if (-not $ResultFile) { $ResultFile = Join-Path $Work 'agent-fix-result.json' }
$LogFile = Join-Path $Work 'agent-fix.log'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Steps = New-Object System.Collections.ArrayList

function Say([string]$m) {
  $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $m
  Write-Host $line
  Add-Content -Path $LogFile -Value $line -Encoding UTF8
  [void]$Steps.Add($m)
}

function Finish([bool]$ok, [string]$msg, [hashtable]$extra) {
  $r = [ordered]@{ ok = $ok; mode = $Mode; message = $msg; agentDir = $AgentDir; steps = @($Steps); time = (Get-Date).ToString('s') }
  if ($extra) { foreach ($k in $extra.Keys) { $r[$k] = $extra[$k] } }
  ($r | ConvertTo-Json -Depth 4) | Set-Content -Path $ResultFile -Encoding UTF8
  Add-Content -Path $LogFile -Value ("=> " + $msg) -Encoding UTF8
  if ($ok) { Write-Host "`n[OK] $msg" -ForegroundColor Green } else { Write-Host "`n[X] $msg" -ForegroundColor Red }
  if ($NoPause) { Start-Sleep -Seconds 4 } else { Read-Host 'กด Enter เพื่อปิดหน้าต่าง' | Out-Null }
  if ($ok) { exit 0 } else { exit 1 }
}

function Get-JavaVersion([string]$jre) {
  $rel = Join-Path $jre 'release'
  if (-not (Test-Path $rel)) { return '' }
  $m = Select-String -Path $rel -Pattern '^JAVA_VERSION="?([^"]+)"?' | Select-Object -First 1
  if ($m) { return $m.Matches[0].Groups[1].Value }
  return ''
}

function Find-AgentDir {
  $svc = Get-CimInstance Win32_Service -Filter "Name='$Service'" -ErrorAction SilentlyContinue
  if ($svc -and $svc.PathName) {
    $p = $svc.PathName.Trim()
    if ($p.StartsWith('"')) { $p = $p.Substring(1, $p.IndexOf('"', 1) - 1) }
    elseif ($p -match '(?i)^(.*?\.exe)') { $p = $Matches[1] }
    return (Split-Path -Parent $p)
  }
  foreach ($c in @('C:\NHSOSecureSmartCardAgent', (Join-Path $env:ProgramFiles 'NHSOSecureSmartCardAgent'))) {
    if (Test-Path (Join-Path $c 'secureagent.jar')) { return $c }
  }
  return ''
}

function Wait-Health([int]$sec) {
  $end = (Get-Date).AddSeconds($sec)
  while ((Get-Date) -lt $end) {
    try {
      $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri 'http://localhost:8189/actuator/health'
      if ($r.StatusCode -eq 200) { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  return $false
}

function Test-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  return ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Stop-Agent($svc) {
  if ($svc) {
    Say 'หยุด service NHSOSecureSmartCardAgent'
    Stop-Service -Name $Service -Force
    (Get-Service -Name $Service).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    Start-Sleep -Seconds 2
  } else {
    Say 'ไม่พบ service — ถ้าเปิด Agent ด้วย start-no-service.bat ให้ปิดหน้าต่างนั้นก่อน'
  }
}

function Start-Agent($svc) {
  if ($svc) {
    Say 'เปิด service NHSOSecureSmartCardAgent'
    Start-Service -Name $Service
    if (Wait-Health 90) { Say 'Agent ทำงานแล้ว (http://localhost:8189/actuator/health)'; return $true }
    Say 'Agent ยังไม่ตอบภายใน 90 วินาที — ดู log ในโฟลเดอร์ logs ของ Agent'
    return $false
  }
  Say 'เปิด Agent เองด้วย start-no-service.bat'
  return $true
}

# ---------- หาโฟลเดอร์ Agent ----------
if (-not $AgentDir) { $AgentDir = Find-AgentDir }
if (-not $AgentDir -or -not (Test-Path (Join-Path $AgentDir 'secureagent.jar'))) {
  Finish $false 'ไม่พบโฟลเดอร์ NHSO Secure SmartCard Agent (ที่มี secureagent.jar) — ระบุด้วย -AgentDir' $null
}
$AgentDir = (Resolve-Path $AgentDir).Path
$Jre = Join-Path $AgentDir 'jre'
$Old = Join-Path $AgentDir 'jre_old'
$Ver = Get-JavaVersion $Jre
Say "โฟลเดอร์ Agent: $AgentDir"
Say ("Java ปัจจุบัน: " + $(if ($Ver) { $Ver } else { 'ไม่ทราบ' }))

if ($Mode -eq 'check') { Finish $true 'ตรวจเสร็จ' @{ javaVersion = $Ver; hasBackup = (Test-Path $Old) } }

# ---------- ต้องเป็น Administrator ----------
if (-not (Test-Admin)) {
  Say 'ขอสิทธิ์ Administrator…'
  $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"", '-Mode', $Mode, '-AgentDir', "`"$AgentDir`"", '-ResultFile', "`"$ResultFile`"")
  if ($ZipPath) { $a += @('-ZipPath', "`"$ZipPath`"") }
  if ($NoPause) { $a += '-NoPause' }
  try { Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $a | Out-Null; exit 0 }
  catch { Finish $false 'ไม่ได้รับสิทธิ์ Administrator — คลิกขวา fix-agent.bat แล้วเลือก Run as administrator' $null }
}

$Svc = Get-Service -Name $Service -ErrorAction SilentlyContinue

# ---------- คืนค่าเดิม ----------
if ($Mode -eq 'rollback') {
  if (-not (Test-Path $Old)) { Finish $false 'ไม่มีโฟลเดอร์ jre_old ให้คืนค่า' $null }
  Stop-Agent $Svc
  if (Test-Path $Jre) { Rename-Item -Path $Jre -NewName ('jre_failed_' + $Stamp); Say "เก็บ jre ปัจจุบันไว้เป็น jre_failed_$Stamp" }
  Rename-Item -Path $Old -NewName 'jre'
  Say 'คืน jre_old กลับเป็น jre แล้ว'
  $up = Start-Agent $Svc
  Finish $up ('คืนค่า Java เดิม (' + (Get-JavaVersion $Jre) + ') แล้ว') @{ javaVersion = (Get-JavaVersion $Jre); hasBackup = (Test-Path $Old) }
}

# ---------- อัปเดต JRE 8 ----------
# 1) ไฟล์ ZIP: ที่ระบุ > ที่วางไว้เอง (ข้างสคริปต์ / โฟลเดอร์ Agent / ProgramData) > ดาวน์โหลดจาก adoptium
if (-not $ZipPath) {
  foreach ($d in @($PSScriptRoot, $AgentDir, $Work)) {
    $f = Get-ChildItem -Path $d -Filter 'OpenJDK8U-jre_x64_windows*.zip' -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { $ZipPath = $f.FullName; break }
  }
}
if ($ZipPath) {
  Say "ใช้ไฟล์ที่มีอยู่: $ZipPath"
} else {
  $ZipPath = Join-Path $Work ('temurin8-jre-' + $Stamp + '.zip')
  Say 'ดาวน์โหลด JRE 8 (Eclipse Temurin, Windows x64) จาก adoptium.net…'
  try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor 3072
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -UseBasicParsing -Uri $JreUrl -OutFile $ZipPath
  } catch {
    Finish $false ("ดาวน์โหลดไม่สำเร็จ (" + $_.Exception.Message + ") — ดาวน์โหลด JRE 8 แบบ ZIP เองจาก https://adoptium.net/temurin/releases?version=8 แล้ววางไว้ที่ $Work แล้วกดซ่อมอีกครั้ง") $null
  }
}

# 2) แตกไฟล์และตรวจว่าเป็น JRE 8 จริง
$X = Join-Path $Work ('jre_new_' + $Stamp)
try { Expand-Archive -Path $ZipPath -DestinationPath $X -Force }
catch { Finish $false ('แตกไฟล์ ZIP ไม่สำเร็จ: ' + $_.Exception.Message) $null }
$NewJre = Get-ChildItem -Path $X -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'bin\java.exe') } | Select-Object -First 1
$NewPath = if ($NewJre) { $NewJre.FullName } elseif (Test-Path (Join-Path $X 'bin\java.exe')) { $X } else { '' }
if (-not $NewPath) { Finish $false 'ไฟล์ ZIP ไม่ใช่ JRE (ไม่พบ bin\java.exe)' $null }
$NewVer = Get-JavaVersion $NewPath
if (-not $NewVer.StartsWith('1.8')) { Finish $false "ไฟล์นี้เป็น Java $NewVer — Agent ต้องใช้ JRE 8 เท่านั้น" $null }
Say "JRE ใหม่: $NewVer"

# 3) หยุด Agent -> jre เดิมเป็น jre_old -> วาง jre ใหม่ -> เปิด Agent
Stop-Agent $Svc
$Moved = ''
try {
  if (Test-Path $Jre) {
    if (Test-Path $Old) { $Moved = 'jre_prev_' + $Stamp } else { $Moved = 'jre_old' }
    Rename-Item -Path $Jre -NewName $Moved
    Say "เปลี่ยนชื่อ jre เดิมเป็น $Moved"
  }
  Copy-Item -Path $NewPath -Destination $Jre -Recurse
  Say 'วาง jre ใหม่แล้ว'
} catch {
  Say ('ผิดพลาดระหว่างเปลี่ยนโฟลเดอร์: ' + $_.Exception.Message)
  if ($Moved -and -not (Test-Path (Join-Path $Jre 'bin\java.exe'))) {
    if (Test-Path $Jre) { Remove-Item -Recurse -Force $Jre }
    Rename-Item -Path (Join-Path $AgentDir $Moved) -NewName 'jre'
    Say 'คืน jre เดิมกลับแล้ว'
  }
  Start-Agent $Svc | Out-Null
  Finish $false 'เปลี่ยน JRE ไม่สำเร็จ — คืนค่าเดิมแล้ว (ปิดโปรแกรมที่เปิดไฟล์ในโฟลเดอร์ Agent อยู่ แล้วลองใหม่)' $null
}
Remove-Item -Recurse -Force $X -ErrorAction SilentlyContinue
$up = Start-Agent $Svc
if ($up) {
  Finish $true "อัปเดต Java ของ Agent เป็น $NewVer แล้ว — ลองเสียบบัตรอ่านใหม่ (ถ้ายังขึ้น PKIX ให้ IT ตรวจ firewall ที่ทำ SSL inspection)" @{ javaVersion = $NewVer; previous = $Ver; hasBackup = $true }
}
Finish $false "วาง JRE $NewVer แล้ว แต่ Agent ยังไม่ตอบ — ดู log ของ Agent หรือกด “คืนค่าเดิม”" @{ javaVersion = $NewVer; previous = $Ver; hasBackup = $true }
