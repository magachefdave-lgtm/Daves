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

## 화면 디자인 원칙

산업용 모니터링 화면(ISA-101 "High Performance HMI" 계열)의 원칙을 따른다.

- 바탕과 정상 상태는 무채색. 색은 **응답 없음(빨강)** 과 **접속 중(파랑)** 에만 쓴다.
- 상태는 색만으로 구분하지 않고 모양(점 / 네모 / 빈 동그라미 / 막대)도 다르게 한다.
- 설비 50여 대가 스크롤 없이 한 화면에 들어오도록 버튼 폭을 창에 맞춰 줄인다.
- 질감: 은회색 바탕 → 그룹 받침판 → 흰 설비 버튼의 세 겹. 테두리는 가늘게, 그림자는 옅게,
  받침판과 버튼의 모서리는 같은 중심(16 = 6 + 10)을 갖는다. 위쪽 바는 창에 붙이지 않고 띄운다.
- 움직임: 처음 켤 때 그룹이 차례로 떠오르고, 버튼은 마우스를 올리면 1px 뜨고 누르면 내려앉는다.
  색 전환은 모두 빨리 출발해 천천히 멎는 곡선(`ease`)을 쓴다.
- 색 값은 `launcher/ui.py` 맨 위의 상수에 모여 있다.

참고: [Going Gray: A New HMI Standard](https://control.com/technical-articles/going-gray/),
[SoftPerfect PingMonitor tile view](https://www.softperfect.com/products/toolkit/pingmonitor/manual/tile-view.htm),
[Windows 앱 색 지침](https://learn.microsoft.com/windows/apps/design/style/color)
