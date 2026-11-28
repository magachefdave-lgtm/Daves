@echo off
cd /d "%~dp0"
echo EM 평가 현황 대시보드를 시작합니다...
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_app.py
  goto end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python run_app.py
  goto end
)
echo Python 3.8 이상이 필요합니다. 설치 후 다시 실행하세요.
:end
pause
