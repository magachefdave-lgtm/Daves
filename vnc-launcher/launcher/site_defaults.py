"""부서에 배포할 때 미리 넣어 둘 값.

SHARED_EQUIPMENT_FILE 에 부서 공유 설비 목록 경로를 적고 빌드하면, 처음 실행하는 사람의
설정 화면에 그 경로가 미리 채워진다. 비워 두면 각자 PC에만 목록을 둔다.
예: r"\\\\fileserver\\설비팀\\원격접속\\equipment.json"
"""
SHARED_EQUIPMENT_FILE = ""
