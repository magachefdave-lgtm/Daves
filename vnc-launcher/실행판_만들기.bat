@echo off
rem EquipmentRemote.exe 만들기 - Python 3.10 이상이 설치된 Windows PC에서 한 번만 실행
rem (pip 로 pyinstaller 를 받아야 하므로 인터넷 또는 사내 PyPI 미러가 필요합니다)
setlocal
cd /d "%~dp0"

echo [1/3] 빌드 도구 설치
py -3 -m pip install --upgrade pyinstaller pytest
if errorlevel 1 goto nopip

echo [2/3] 테스트
py -3 -m pytest -q tests
if errorlevel 1 goto fail

echo [3/3] exe 빌드
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole --name EquipmentRemote ^
  --exclude-module pytest --exclude-module _pytest --exclude-module pluggy equipment_remote.py
if errorlevel 1 goto fail

copy /y "시작하기.txt" dist\ >nul
copy /y "설비목록_예시.csv" dist\ >nul
copy /y "비밀번호_예시.csv" dist\ >nul
if not exist "dist\시작하기.txt" goto fail

echo.
echo 완료: dist 폴더의 내용을 통째로 사용할 PC에 복사하세요.
pause
exit /b 0

:nopip
echo.
echo 빌드 도구(pyinstaller)를 내려받지 못했습니다.
echo  - 이 PC가 인터넷(pypi.org)에 연결되지 않았거나 회사 보안 설정으로 막힌 경우가 대부분입니다.
echo  - exe 없이 쓰려면: 같은 폴더의 실행.bat 을 실행하세요. (추가 설치 없이 Python 만으로 실행됩니다)
echo  - exe 가 필요하면: 인터넷이 되는 PC에서 이 파일을 실행하거나, 미리 만들어진 exe 를 받아 쓰세요.
pause
exit /b 1

:fail
echo.
echo 실패했습니다. 위의 오류 내용을 확인하세요. exe 는 만들어지지 않았습니다.
pause
exit /b 1
