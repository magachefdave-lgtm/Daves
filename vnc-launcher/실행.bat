@echo off
rem Python이 설치된 PC에서 exe 없이 바로 실행
cd /d "%~dp0"
start "" pyw -3 equipment_remote.py
if errorlevel 1 (
  echo Python을 찾을 수 없습니다. python.org 에서 Python 3.10 이상을 설치하거나,
  echo 실행판_만들기.bat 으로 만든 EquipmentRemote.exe 를 사용하세요.
  pause
)
