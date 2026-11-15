# 설비 원격 접속 (VNC 런처)

설비 목록을 관리하고, 설치된 VNC 뷰어와 Windows 공유폴더를 버튼 하나로 여는 Windows용 프로그램.
파이썬 표준 라이브러리(tkinter)만 사용한다.

- 사용법: [시작하기.txt](시작하기.txt)
- 바로 실행: `실행.bat` (Python 3.10+ 필요) 또는 `python equipment_remote.py`
- exe 만들기: Windows PC에서 `실행판_만들기.bat` → `dist/EquipmentRemote.exe`
- 테스트: `python -m pytest -q tests`

| 파일 | 내용 |
| --- | --- |
| `launcher/core.py` | 설비 목록 저장, CSV, 설정, VNC 명령 구성 |
| `launcher/secret.py` | VNC 비밀번호 보관 (Windows DPAPI) |
| `launcher/ui.py` | 화면 |

`.bat` 파일은 CP949 + CRLF 로 저장해야 한다 (UTF-8 로 저장하면 한글 cmd 에서 깨진다).
