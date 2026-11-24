# 설비 원격 접속 - 인수인계 (handoff)

기준: **v1.4.6** (2026-11-24). 이 문서만 읽고 이어서 고치고, 빌드하고, 배포할 수 있게 쓴다.

## 1. 지금 상태

| 항목 | 내용 |
| --- | --- |
| 배포 중인 버전 | v1.4.6 (`launcher/__init__.py` 의 `APP_VERSION`) |
| 배포 파일 | `EquipmentRemote.exe` 하나 (사용법·CSV 양식 포함, 약 12.8MB) |
| 저장소 | `magachefdave-lgtm/daves`, 브랜치 `ccr-b9d67696-e8fnho`, 소스는 `vnc-launcher/` |
| 빌드된 exe | 저장소의 `release/EquipmentRemote.exe` (CI 가 올림) |
| 테스트 | `pytest` 79개 통과. 화면 조작 점검 87개(개발 중 임시 스크립트, 저장소에 없음 - 7장) |
| 사용 환경 | 한글 Windows 10/11, 사내망. 사용자 PC 의 VNC 뷰어는 주로 **UltraVNC** |

## 2. 무엇을 하는 프로그램인가

설비 약 50대(Windows / Linux)에 VNC 로 접속하는 런처. 설비 이름 버튼을 누르면 설치된 VNC 뷰어를
그 설비 주소와 저장해 둔 비밀번호로 띄운다. Windows 설비는 공유폴더(`\\IP\공유`)와 원격 데스크톱(RDP) 버튼도 있다.

주요 기능: 그룹, 즐겨찾기, 검색, 주의 표시(PM 중 등), 상태 확인(응답 없음 표시), 접속 중 표시와
뷰어 창 제목에 설비명, 이미 열린 창 앞으로 가져오기, 여러 대 선택, 편집 잠금(관리자 PIN), 접속 이력,
날짜별 백업, CSV 가져오기/내보내기, 배포본(설정을 넣은 exe), 서버 폴더를 통한 자동 업데이트.

파이썬 **표준 라이브러리만** 쓴다 (tkinter, ctypes). 외부 패키지 없음 - 사내망에서 그대로 돈다.
프로그램은 인터넷에 접속하지 않는다 (설비, 공유 폴더, 업데이트 폴더만).

## 3. 소스 구조

| 경로 | 내용 |
| --- | --- |
| `equipment_remote.py` | 시작점. 명령줄 옵션(`--reset-pin`, `--pack-info`, `--version-out`, `--install-update`)과 실행 오류 안내 파일 |
| `launcher/__init__.py` | `APP_NAME`, `APP_VERSION` |
| `launcher/core.py` | 설비(`Equipment`), 저장소(`EquipmentStore`: 공유 목록, 3방향 병합, 백업, 편집 잠금), CSV, 설정(`Config`), 뷰어 명령(`VIEWERS`, `build_command`), 상태 확인(`probe`, `ping`, `is_reachable`), 경로(`app_dir`, `exe_dir`) |
| `launcher/ui.py` | 화면 전체 (~2800줄). `App` 클래스, 대화상자들. Canvas 에 직접 그린다 |
| `launcher/local.py` | 이 PC 에만 남기는 것: 즐겨찾기, 창 위치, 마지막 그룹, 관리자 메뉴 표시, 가져온 배포본 id |
| `launcher/pack.py` | 배포본: exe 끝에 설정·목록·비밀번호를 붙인다 |
| `launcher/update.py` | 업데이트 폴더에 올리기(`publish`), 받아서 바꾸기(`install`), 다시 켜기(`relaunch`) |
| `launcher/secret.py` | VNC 비밀번호 보관 (Windows DPAPI, 사용자 계정별 암호화) |
| `launcher/history.py` | 접속 이력 (달마다 CSV) |
| `launcher/wintitle.py` | Windows 창 다루기 (뷰어 창 제목 바꾸기, 앞으로 가져오기, 화면 크기) |
| `launcher/site_defaults.py` | 빌드 때 미리 넣을 공유 목록 경로·업데이트 폴더 (비어 있음) |
| `launcher/icon_data.py`, `assets/` | 아이콘 (`tools/make_icon.py` 로 다시 만든다, Pillow 필요) |
| `tests/` | pytest (core, local, pack, update, icon) |
| `tools/vnc_server_check.py` | 실제 VNC 서버에 대고 "상태 확인이 서버를 막히게 하지 않는가" 시험 (CI 에서 씀, pycryptodome 필요) |
| `시작하기.txt` | 사용자 안내서 (exe 안에 들어가 [더보기 > 사용법 보기]로 열림). UTF-8 BOM + CRLF |
| `설비목록_예시.csv`, `비밀번호_예시.csv` | CSV 양식 (exe 안에 들어감) |
| `실행.bat`, `실행판_만들기.bat` | Python 으로 바로 실행 / Windows 에서 exe 만들기. **CP949 + CRLF** |
| `../.github/workflows/build-vnc-launcher.yml` | Windows 빌드·시험·배포 (5장) |
| `../.github/workflows/vnc-server-check.yml` | 실제 VNC 서버 시험 (6장) |

## 4. 실행, 테스트, 빌드

```
cd vnc-launcher
python equipment_remote.py          # 개발 중 실행 (Python 3.10+)
python -m pytest -q tests           # 단위 테스트
```

exe 빌드 (Windows, `실행판_만들기.bat` 과 같은 명령):

```
py -3 -m pip install pyinstaller
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole --name EquipmentRemote --icon assets\app.ico ^
  --add-data "시작하기.txt;." --add-data "설비목록_예시.csv;." --add-data "비밀번호_예시.csv;." ^
  --exclude-module pytest --exclude-module _pytest --exclude-module pluggy equipment_remote.py
```

→ `dist\EquipmentRemote.exe`. 회사 PC 에서는 pip 가 막혀 빌드가 안 됐다. 그래서 **GitHub Actions 에서 빌드한다** (5장).

## 5. CI 와 배포 흐름

`build-vnc-launcher.yml` (windows-latest, `vnc-launcher/**` 가 바뀌면 돈다):
1. pytest → 2. PyInstaller 빌드 → 3. 배포본을 실제로 만들어 Windows 에서 실행해 봄 →
4. 업데이트(이름 바꾸기 + 다시 켜기)를 실제로 해 봄 → 5. artifact 업로드 →
6. 커밋 메시지에 **`[publish-zip]`** 이 있으면 봇이 `release/EquipmentRemote.exe` 를 같은 브랜치에 커밋한다.

새 버전 내는 순서:
1. 고치고 `APP_VERSION` 올리기 (`launcher/__init__.py`), `시작하기.txt`·README 반영
2. 커밋 메시지 끝에 `[publish-zip]` 을 붙여 push
3. 봇 커밋("Add built EquipmentRemote.exe")이 올라오면 `git pull` → `release/EquipmentRemote.exe`
4. 관리자 PC 에서 새 exe 실행 → Shift+[더보기] > 관리자 > 업데이트 올리기 → 각 PC 에 업데이트 버튼

## 6. 데이터와 파일 위치

| 파일 | 위치 | 내용 |
| --- | --- | --- |
| `equipment.json` | 설정의 "설비 목록 파일" (보통 부서 공유 폴더) | 설비, 그룹 순서, 주의 표시, 편집 잠금(PIN 해시). `"version": 1` |
| `equipment.json.bak`, `backup/목록이름_날짜.json` | 목록 옆 | 직전 상태 + 날짜별 14개 |
| 접속 이력 CSV | 목록 옆 (달마다 한 파일) | 접속/종료/실패 |
| `config.ini` | `%LOCALAPPDATA%\설비원격접속` (exe 옆에 `config.ini` 가 있으면 그 폴더) | 뷰어 종류·경로, 공유 목록 경로, 업데이트 폴더 등 |
| `passwords.json` | 같은 폴더 | 설비별 VNC 비밀번호 (DPAPI 암호화, 다른 PC·계정에서는 못 푼다) |
| `ui_state.json` | 같은 폴더 | 즐겨찾기, 창 위치, `admin`, `applied_pack` |

공유 목록을 여러 PC 가 같이 쓰므로 저장은 항상 "파일을 새로 읽고 → 그 위에 적용 → 임시 파일 → 바꾸기"다.
수정 대화상자는 3방향 병합(`EquipmentStore.update(eq, base)`)으로 다른 PC 가 그사이 바꾼 칸을 덮지 않는다.

## 7. 꼭 알아야 할 설계 결정과 그 이유

**상태 확인과 TigerVNC 차단 (실제 사고).** 처음에는 모든 설비를 VNC 포트에 TCP 로 붙었다 끊는 방식으로
1분마다 확인했다. 리눅스 설비의 TigerVNC(Xvnc, x0vncserver)는 로그인 없이 끊긴 연결을 "보안 실패"로 세어
5번이면 그 PC 를 막는다("Too many security failures", 10초부터 두 배씩). 이미 막힌 동안의 시도는 늘리지 않지만,
풀리자마자 다음 확인이 다시 막는다. 그래서:
- `check_method`: 설비별 `check` = auto / port / ping / off. **auto = Linux 는 ping, 그 밖은 VNC 포트**.
- 연결을 RST 로 끊어도 피할 수 없다 (서버가 주소를 읽은 뒤면 똑같이 센다 - 시험으로 확인).
- Linux 설비를 "VNC 포트"로 바꾸려 하면 경고한다. 막혔을 때 푸는 법: 설비에서 `pgrep -a x0vncserver` →
  `sudo kill <번호>` → 다시 실행 (Xvnc 는 그 위의 프로그램이 같이 꺼지므로 주의).
- 같은 설비를 동시에 두 번 확인하지 않음(`App.inflight`, F5 연타 방지), 창을 내려 두면 자동 확인을 쉰다.

**ping (Windows).** `IcmpSendEcho` 를 ctypes 로 직접 부른다 - 관리자 권한도 ping.exe 창도 필요 없다.
설비가 ping 을 막아 두면 빨간색으로 보이므로, 그런 설비는 상태 확인을 "하지 않음"으로 둔다 (앱 안에서 한 대/여러 대 바꿀 수 있음).

**뷰어.** `VIEWERS` 에 종류별 인자. UltraVNC: `-connect host::port -password pw`, 조용한 접속 `-nostatus`.
UltraVNC 1.8 이후는 `C:\Program Files\uvnc\UltraVNC\` 에 깔린다 (예전 `uvnc bvba`) - 둘 다 찾는다.
TigerVNC 뷰어는 기본이 단독 접속이라 `-Shared` 를 붙인다. 창 제목에 설비명은 `wintitle.py` 가 WM_SETTEXT 로 바꾼다.

**배포본 (`pack.py`).** exe 끝에 `[MAGIC][JSON][길이 8바이트][TRAILER]` 를 붙인다 (PyInstaller 는 끝에서 자기 내용을 찾으므로 실행에 지장 없음).
비밀번호는 DPAPI 로는 옮길 수 없어서 배포 암호(PBKDF2 → HMAC-SHA256 키스트림 + HMAC 확인값)로 다시 암호화한다.
배포 암호 없이 만들면 고정 키라 exe 를 가진 사람은 꺼낼 수 있다. 배포본을 가져온 PC 는 `admin=False` (관리자 메뉴 숨김).

**업데이트 (`update.py`).** 업데이트 폴더에 `EquipmentRemote.exe` + `latest.json`(version, sha256, notes).
받는 쪽: `.new` 로 복사 → sha256 확인 → 실행 중인 exe 를 `.old` 로 이름만 바꿈 → `.new` 를 원래 이름으로 → 다시 켬.
다시 켤 때 `PYINSTALLER_RESET_ENVIRONMENT=1` 을 줘야 한다 (안 주면 새 exe 가 지금 exe 의 임시 폴더를 빌려 쓰다 죽는다).
exe 가 공유 폴더에 있거나 그 폴더에 쓸 수 없으면 설정 폴더에 받는다. 켤 때 + 3시간마다 확인.

**관리자 메뉴.** 배포본 만들기·업데이트 올리기·편집 잠금 설정은 `LocalState.admin` 이 참일 때만 [더보기]에 보인다
(정하지 않았으면 배포본으로 설치한 PC 가 아니면 관리자). Shift+[더보기]로 언제든 열 수 있고, 편집 잠금 PIN 이 있으면 PIN 을 묻는다.
PIN 을 잊으면 `EquipmentRemote.exe --reset-pin`.

**공유 목록 보호.** 읽던 파일이 안 보이면(네트워크 끊김) 빈 목록으로 여기지 않고 오류로 다룬다 → 목록을 지우는 저장이 생기지 않는다.
이 버전이 모르는 항목(새 버전이 넣은 칸)은 저장할 때 그대로 둔다. 파일의 `version` 이 `FORMAT_VERSION` 보다 크면 읽기만 한다.
임시 파일 이름은 PC·프로세스마다 다르고, 바꾸기는 잠깐씩 재시도한다 (다른 PC 가 읽는 중이면 Windows 가 거절).

**IP.** `192.168.010.011` 은 Windows 가 8진수로 읽어 192.168.8.9 로 간다 → `normalize_host` 가 0 을 뗀다.
`192.168.10`, `0x0a000001` 같은 줄임 표기는 `validate` 가 막는다.

**화면.** ISA-101(산업용 HMI) 원칙: 정상은 표시 없음, 이상만 색 + 모양(■ 응답 없음, ▲ 주의) + 왼쪽 막대.
50대가 스크롤 없이 들어가도록 칸 수를 계산한다. 뷰어가 여러 개 떠도 버튼 배치가 흔들리지 않게 위쪽 "접속 중" 표시는 고정 폭 한 칸이다.

## 8. 함정 (실제로 겪은 것)

- `.bat` 은 **CP949 + CRLF** 로 저장 (UTF-8 이면 한글 cmd 에서 깨짐). `시작하기.txt` 는 UTF-8 BOM + CRLF.
- zip 안의 한글 파일 이름은 Windows 탐색기에서 깨질 수 있다 - 배포 zip 은 영문 이름으로.
- 회사 PC: pip/외부 사이트가 막혀 있다. Claude 앱에서 12MB exe(와 zip)는 "너무 크다"며 받지 못했다 → 3MB 조각 + `copy /b` 로 합치기.
  Google Drive 커넥터로 올리는 것도 한 번에 약 3MB 까지였다.
- CI Windows 콘솔은 cp1252 라 파이썬이 한글을 print 하면 죽는다 → `PYTHONUTF8=1`.
- Chocolatey 공용 서버는 자주 504/503 → 서버 시험의 설치 단계는 재시도 + 실패해도 빌드를 막지 않게 했다.
- GUI 시험을 Xvfb 에서 할 때 창 관리자가 없으면 최소화(iconify)가 안 된다 → twm 을 같이 띄운다.
- tkinter `<Map>` 은 자식 위젯에서도 오므로 `event.widget is root` 를 확인한다.

## 9. 실제 VNC 서버 시험 결과 (`vnc-server-check.yml`, `tools/vnc_server_check.py`)

| 서버 | 설비 / 확인 방식 | 시험 | 그 뒤 VNC 로그인 |
| --- | --- | --- | --- |
| TigerVNC Xvnc, x0vncserver | Linux / ping (기본) | 300번 | 정상 |
| TigerVNC Xvnc | Linux / VNC 포트 | 8번 (3초 간격) | Too many security failures (사고 재현, 경고의 근거) |
| x11vnc | Linux / ping, VNC 포트 | 각 300번 | 정상 |
| TightVNC | Windows / VNC 포트 (기본) | 300번 | 정상 |
| UltraVNC 1.8.3 | Windows / VNC 포트 (기본) | 1초에 300번 | 직후 한 번 거절, 4초 뒤 정상 (한 번 수동 시험) |
| UltraVNC 1.8.3 | Windows / ping | 50번 | 정상 |

리눅스 세 가지는 프로그램을 고칠 때마다 CI 가 다시 시험한다. TightVNC 는 패키지 서버가 될 때만. UltraVNC 는 요청에 따라 CI 에서 뺐다.

## 10. 알고 남겨 둔 것

- UltraVNC 뷰어에 비밀번호를 명령줄로 넘긴다 (그 뷰어의 방식). 보안 프로그램이 실행 명령줄을 기록하면 남을 수 있다.
  대안: 비밀번호를 넣은 임시 `.vnc` 설정 파일로 넘기기 - 아직 안 함.
- 접속 이력 CSV 를 공유 폴더에 두면 여러 PC 가 동시에 쓸 때 드물게 한 줄이 빠질 수 있다.
- TigerVNC/RealVNC 서버가 깔린 **Windows** 설비를 VNC 포트로 확인하면 리눅스와 같은 차단이 생길 수 있다 → 그 설비만 ping 으로.
- 1.4.4 이하 PC 가 공유 목록을 저장하면 상태 확인 방식 같은 새 설정이 지워질 수 있다 → 모두 1.4.5 이상으로.
- 화면 조작 점검(87개)은 개발 중 임시 스크립트(Xvfb + 실제 화면 조작)로 돌렸고 저장소에 넣지 않았다. 화면을 크게 바꾸면 비슷한 점검을 다시 만드는 것을 권한다.

## 11. 버전 이력 (요약)

| 버전 | 날짜 | 바뀐 것 |
| --- | --- | --- |
| 1.4.6 | 2026-11-24 | 새 UltraVNC(1.8 이후) 설치 폴더에서도 뷰어를 찾음 |
| 1.4.5 | 2026-11-24 | 관리자 메뉴 숨김, 외부 영향 점검 10건 반영 (IP, 공유 목록 보호, F5, 최소화 중 확인 쉼, 업데이트 3시간, RDP 확인 등) |
| 1.4.4 | 2026-11-24 | 녹색 점 제거, 이상 상태만 왼쪽 막대로 |
| 1.4.3 | 2026-11-24 | 뷰어 여러 개일 때 버튼 흔들림 수정, 최근 접속 제거 |
| 1.4.2 | 2026-11-24 | 앱 안에서 상태 확인 방식 바꾸기 (한 대/여러 대) |
| 1.4.1 | 2026-11-23 | 리눅스 설비는 ping 으로 확인 (TigerVNC 차단 사고 수정) |
| 1.4.0 이전 | 2026-11-15 ~ 23 | 버튼 화면, CSV, 그룹, 디자인, 즐겨찾기·주의·잠금·이력·백업·RDP, 아이콘, exe 하나, 배포본, 서버 업데이트 |

## 12. 고칠 때 체크리스트

- [ ] `python -m pytest -q tests` 통과
- [ ] 설비·서버에 새로 연결하거나 파일을 쓰는 코드라면: 30대 PC × 하루 종일 켜 둔다고 보고 횟수·동시성을 따진다
- [ ] 상태 확인을 바꿨다면 `vnc-server-check.yml` 결과 확인
- [ ] `APP_VERSION` 올림, `시작하기.txt`·README 반영 (`.bat` 은 CP949, 안내서는 UTF-8 BOM)
- [ ] 커밋 메시지에 `[publish-zip]` → 봇 커밋 → exe 확인 → 업데이트 폴더에 올리기
