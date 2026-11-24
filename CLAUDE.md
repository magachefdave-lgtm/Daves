# 작업 규칙

## 대화
- 답변과 사고 과정은 한국어로 쓴다.

## 결과물 전달 (설비 원격 접속, `vnc-launcher/`)

사용자의 회사 PC 에서는 Claude 앱의 파일 카드로 실행 파일을 받을 수 없다 (exe 는 다운로드 버튼이 없고,
12MB 대 파일은 "너무 크다"며 실패). **GitHub 링크로 전달한다.** 저장소는 공개라 로그인 없이 받아진다.

1. 고친 뒤 `launcher/__init__.py` 의 `APP_VERSION` 을 올리고, 커밋 메시지 끝에 `[publish-zip]` 을 붙여 push.
2. 빌드 봇 커밋("Add built EquipmentRemote.exe")이 올라오면 `git pull`, 그 커밋의 전체 SHA 를 확인.
3. 채팅에 두 링크를 준다:
   - 버전 고정: `https://github.com/magachefdave-lgtm/Daves/raw/<봇 커밋 전체 SHA>/release/EquipmentRemote.exe`
   - 늘 최신: `https://github.com/magachefdave-lgtm/Daves/raw/ccr-b9d67696-e8fnho/release/EquipmentRemote.exe`
4. 주기 전에 `curl -sSL -o 파일 -w "%{http_code} %{size_download}" <링크>` 로 실제로 받아지는지,
   sha256 이 `release/EquipmentRemote.exe` 와 같은지 확인한다.

문서(PDF·HTML·소스 zip 등)도 같은 방식: 저장소(예: `vnc-launcher/docs/` 또는 `release/`)에 커밋하고
`https://github.com/magachefdave-lgtm/Daves/raw/<커밋 SHA>/<경로>` 링크를 준다. 파일 카드나 조각 나누기는 쓰지 않는다.

같은 브랜치에서 다른 Claude 세션도 작업한다. push 전에 `git fetch` 로 원격 커밋을 확인하고, 있으면 합친 뒤 올린다.
자세한 구조와 빌드는 `vnc-launcher/handoff.md`.
