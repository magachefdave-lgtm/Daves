"""부서에 배포할 때 미리 넣어 둘 값.

SHARED_EQUIPMENT_FILE 에 부서 공유 설비 목록 경로를 적고 빌드하면, 처음 실행하는 사람의
설정 화면에 그 경로가 미리 채워진다. 비워 두면 각자 PC에만 목록을 둔다.
예: r"\\\\fileserver\\설비팀\\원격접속\\equipment.json"
"""
SHARED_EQUIPMENT_FILE = ""

# 업데이트 폴더: 관리자가 [더보기 > 업데이트 올리기]로 새 버전을 올리는 서버(공유) 폴더.
# 예: r"\\fileserver\설비팀\원격접속\update"
UPDATE_FOLDER = ""

# 업데이트 서명 키(공개 키, 64자리 16진수). 적어 두면 이 키로 서명한 업데이트만 받는다.
# 비워 두면 각 PC가 처음 본 서명 키(또는 배포본에 들어 있는 키)를 믿는다.
UPDATE_PUBLIC_KEY = ""
