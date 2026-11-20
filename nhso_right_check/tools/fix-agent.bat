@echo off
rem NHSO Secure SmartCard Agent - check / fix (update JRE 8 Temurin). Right-click > Run as administrator
rem   fix-agent.bat           = fix (update JRE 8)
rem   fix-agent.bat check     = check only
rem   fix-agent.bat rollback  = restore previous jre
chcp 65001 >nul
set MODE=%1
if "%MODE%"=="" set MODE=fix
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix-agent.ps1" -Mode %MODE%
