"""설비 목록 저장, 설정, VNC 명령 구성 - 화면(tkinter)과 무관한 부분."""
from __future__ import annotations

import configparser
import csv
import io
import json
import os
import re
import shutil
import socket
import sys
import uuid
from dataclasses import asdict, dataclass, field

OS_WINDOWS = "Windows"
OS_CHOICES = (OS_WINDOWS, "Linux", "기타")
DEFAULT_PORT = 5900

CSV_COLUMNS = ("설비명", "그룹", "IP", "포트", "OS", "공유폴더", "메모", "공유폴더사용")

_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$")
_SHARE_BAD = re.compile(r'[<>"|?*\x00-\x1f]')


def natural_key(text: str):
    """EQ-2 가 EQ-10 앞에 오도록 숫자를 숫자로 비교한다."""
    return [(0, int(p)) if p.isdigit() else (1, p.casefold()) for p in re.split(r"(\d+)", text)]


class StoreError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 문장을 담는다."""


# ----------------------------------------------------------------- 설비 ---

@dataclass
class Equipment:
    name: str
    host: str
    group: str = ""
    port: int = DEFAULT_PORT
    os: str = OS_WINDOWS
    share: str = ""
    memo: str = ""
    share_enabled: bool = True   # False = 이 설비에는 공유폴더 버튼을 보이지 않는다
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    @classmethod
    def from_dict(cls, d: dict) -> "Equipment":
        return cls(
            name=str(d.get("name", "")).strip(),
            host=str(d.get("host", "")).strip(),
            group=str(d.get("group", "")).strip(),
            port=_to_port(d.get("port", DEFAULT_PORT)),
            os=normalize_os(d.get("os", OS_WINDOWS)),
            share=str(d.get("share", "")).strip(),
            memo=str(d.get("memo", "")).strip(),
            share_enabled=_to_bool(d.get("share_enabled", True)),
            id=str(d.get("id") or uuid.uuid4().hex),
        )


def _to_port(value) -> int:
    text = str(value).strip()
    if not text:
        return DEFAULT_PORT
    try:
        return int(float(text))  # 엑셀이 5900.0 으로 내보내는 경우
    except ValueError:
        return -1


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in (
        "n", "no", "0", "false", "off", "x", "아니오", "아니요", "미사용", "사용안함", "숨김")


def has_share(eq: "Equipment") -> bool:
    """공유폴더 버튼을 쓸 수 있는 설비인지."""
    return eq.os == OS_WINDOWS and eq.share_enabled


def new_title(name: str, current: str) -> str | None:
    """VNC 창 제목 앞에 설비명을 붙인 새 제목. 이미 붙어 있으면 None."""
    prefix = f"[{name}] "
    return None if current.startswith(prefix) else prefix + current


def normalize_os(value) -> str:
    text = str(value or "").strip().lower()
    if not text or text.startswith("win") or text in ("윈도우", "윈도우즈", "윈도"):
        return OS_WINDOWS
    if text in ("linux", "리눅스") or "linux" in text:
        return "Linux"
    return "기타"


def validate(eq: Equipment) -> list[str]:
    """문제 목록을 돌려준다. 비어 있으면 정상."""
    errors = []
    if not eq.name:
        errors.append("설비명을 입력하세요.")
    if not eq.host:
        errors.append("IP(또는 호스트명)를 입력하세요.")
    elif not _HOST_RE.match(eq.host):
        errors.append("IP(또는 호스트명)에는 영문, 숫자, 점(.), 하이픈(-)만 쓸 수 있습니다.")
    if not 1 <= eq.port <= 65535:
        errors.append("포트는 1~65535 사이의 숫자여야 합니다. (VNC 기본값 5900)")
    if eq.os not in OS_CHOICES:
        errors.append("OS는 Windows / Linux / 기타 중 하나여야 합니다.")
    if _SHARE_BAD.search(eq.share):
        errors.append('공유폴더 이름에 쓸 수 없는 글자가 있습니다. ( < > " | ? * )')
    return errors


def share_path(eq: Equipment) -> str:
    r"""탐색기로 열 UNC 경로. 비어 있으면 \\IP (공유 목록 전체)."""
    if eq.os != OS_WINDOWS:
        raise ValueError("공유폴더 접속은 Windows 설비에서만 쓸 수 있습니다.")
    share = eq.share.strip().replace("/", "\\")
    if share.startswith("\\\\"):
        return share.rstrip("\\")
    share = share.strip("\\")
    return "\\\\" + eq.host + ("\\" + share if share else "")


# ------------------------------------------------------------ 목록 저장 ---

class EquipmentStore:
    """JSON 파일 하나에 설비 목록을 둔다.

    파일을 공유 폴더에 두고 여러 PC가 같이 쓸 수 있도록, 바꾸는 동작은 매번
    파일을 새로 읽은 뒤 그 위에 적용한다(다른 PC가 방금 추가한 설비를 덮어쓰지 않게).
    """

    def __init__(self, path: str):
        self.path = path
        self.groups: list[str] = []  # 화면에 보일 그룹 순서 (설비가 없는 빈 그룹 포함)

    @staticmethod
    def _merge_groups(stored, items: list[Equipment]) -> list[str]:
        """저장된 순서를 지키고, 목록에 없는데 설비가 쓰는 그룹은 뒤에 붙인다."""
        out: list[str] = []
        for g in stored:
            g = str(g).strip()
            if g and g not in out:
                out.append(g)
        extra = {e.group for e in items if e.group} - set(out)
        return out + sorted(extra, key=natural_key)

    def mtime(self) -> float:
        try:
            return os.stat(self.path).st_mtime
        except OSError:
            return 0.0

    def load(self) -> list[Equipment]:
        if not os.path.exists(self.path):
            self.groups = []
            return []
        try:
            with open(self.path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            items = [Equipment.from_dict(d) for d in data["equipment"]]
            self.groups = self._merge_groups(data.get("groups") or [], items)
            return items
        except OSError as e:
            raise StoreError(
                f"설비 목록 파일을 읽을 수 없습니다.\n{self.path}\n\n"
                f"공유 폴더에 둔 경우 네트워크 연결과 폴더 권한을 확인하세요.\n({e})"
            ) from e
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise StoreError(
                f"설비 목록 파일이 손상되었습니다.\n{self.path}\n\n"
                f"같은 폴더의 '{os.path.basename(self.path)}.bak' 파일을 원래 이름으로 "
                f"바꾸면 직전 상태로 되돌릴 수 있습니다.\n({e})"
            ) from e

    def _write(self, items: list[Equipment], groups=None) -> None:
        groups = self._merge_groups(self.groups if groups is None else groups, items)
        folder = os.path.dirname(os.path.abspath(self.path))
        tmp = self.path + ".tmp"
        try:
            os.makedirs(folder, exist_ok=True)
            if os.path.exists(self.path):
                shutil.copy2(self.path, self.path + ".bak")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "groups": groups,
                           "equipment": [asdict(e) for e in items]},
                          f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            self.groups = groups
        except OSError as e:
            raise StoreError(
                f"설비 목록 파일에 저장할 수 없습니다.\n{self.path}\n\n"
                f"폴더 쓰기 권한이 있는지, 공유 폴더라면 연결되어 있는지 확인하세요.\n({e})"
            ) from e

    @staticmethod
    def _check(eq: Equipment, items: list[Equipment]) -> None:
        errors = validate(eq)
        key = eq.name.casefold()
        if any(o.id != eq.id and o.name.casefold() == key for o in items):
            errors.append(f"같은 설비명이 이미 있습니다: {eq.name}")
        if errors:
            raise StoreError("\n".join(errors))

    def add(self, eq: Equipment) -> None:
        items = self.load()
        self._check(eq, items)
        items.append(eq)
        self._write(items)

    def update(self, eq: Equipment) -> None:
        items = self.load()
        self._check(eq, items)
        for i, old in enumerate(items):
            if old.id == eq.id:
                items[i] = eq
                break
        else:
            raise StoreError("다른 PC에서 이미 삭제된 설비입니다. 목록을 새로 고쳤습니다.")
        self._write(items)

    def move(self, ids, group: str) -> int:
        """설비를 다른 그룹으로 옮긴다. 없는 그룹이면 새로 만든다. ("" = 그룹 없음)"""
        ids, group = set(ids), group.strip()
        items = self.load()
        moved = 0
        for e in items:
            if e.id in ids and e.group != group:
                e.group = group
                moved += 1
        if moved or (group and group not in self.groups):
            self._write(items, self.groups + [group])
        return moved

    def apply_groups(self, groups: list[str], assign: dict[str, str]) -> None:
        """그룹 관리 화면의 결과를 저장한다.

        groups: 그룹 이름과 순서, assign: {설비 id: 그룹}. assign 에 없는 설비
        (그 사이 다른 PC가 추가한 것)는 건드리지 않는다.
        """
        groups = self._merge_groups(groups, [])
        items = self.load()
        for e in items:
            if e.id in assign:
                g = assign[e.id].strip()
                e.group = g if g in groups else ""
        self._write(items, groups)

    def delete(self, ids) -> int:
        ids = set(ids)
        items = self.load()
        kept = [e for e in items if e.id not in ids]
        if len(kept) != len(items):
            self._write(kept)
        return len(items) - len(kept)

    def import_csv(self, csv_path: str) -> tuple[int, int, list[str], dict[str, str]]:
        """설비명이 같으면 고치고 없으면 추가한다.

        (추가 수, 수정 수, 건너뛴 줄 설명, {설비 id: 비밀번호})
        비밀번호는 목록 파일에 넣지 않고 돌려주기만 한다 - 보관은 PasswordStore 몫.
        IP 열 없이 '설비명,비밀번호'만 있는 파일은 이미 등록된 설비에 비밀번호만 붙인다.
        """
        rows, has_host = read_csv(csv_path)
        items = self.load()
        by_name = {e.name.casefold(): e for e in items}
        added = updated = 0
        problems = []
        passwords: dict[str, str] = {}
        for line_no, row in rows:
            password = row.pop("password", "").strip()
            if not has_host:
                name = row.get("name", "").strip()
                old = by_name.get(name.casefold())
                if not old:
                    problems.append(f"{line_no}번째 줄 ({name or '이름 없음'}): 등록되지 않은 설비명입니다.")
                elif password:
                    passwords[old.id] = password
                continue
            eq = Equipment.from_dict(row)
            errors = validate(eq)
            if errors:
                problems.append(f"{line_no}번째 줄 ({eq.name or '이름 없음'}): {errors[0]}")
                continue
            old = by_name.get(eq.name.casefold())
            if old:
                eq.id = old.id
                if "share_enabled" not in row:   # 열이 없는 CSV는 기존 설정을 지킨다
                    eq.share_enabled = old.share_enabled
                items[items.index(old)] = eq
                updated += 1
            else:
                items.append(eq)
                added += 1
            by_name[eq.name.casefold()] = eq
            if password:
                passwords[eq.id] = password
        if added or updated:
            self._write(items)
        return added, updated, problems, passwords


_CSV_KEYS = {
    "설비명": "name", "이름": "name", "name": "name",
    "그룹": "group", "라인": "group", "group": "group",
    "ip": "host", "host": "host", "주소": "host", "ip주소": "host",
    "포트": "port", "port": "port",
    "os": "os", "운영체제": "os",
    "공유폴더": "share", "share": "share",
    "메모": "memo", "비고": "memo", "memo": "memo",
    "공유폴더사용": "share_enabled", "공유폴더버튼": "share_enabled",
    "비밀번호": "password", "vnc비밀번호": "password", "암호": "password",
    "password": "password", "pw": "password",
}


def read_csv(csv_path: str) -> tuple[list[tuple[int, dict]], bool]:
    """엑셀이 저장한 CSV(CP949)와 UTF-8 둘 다 읽는다. ([(줄 번호, 행)], IP 열이 있는지)"""
    try:
        with open(csv_path, "rb") as f:
            raw = f.read()
    except OSError as e:
        raise StoreError(f"CSV 파일을 열 수 없습니다.\n{csv_path}\n({e})") from e
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp949", errors="replace")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, None)
    if header is None:
        raise StoreError("CSV 파일이 비어 있습니다.")
    keys = [_CSV_KEYS.get(h.strip().replace(" ", "").lower()) for h in header]
    if "name" not in keys or ("host" not in keys and "password" not in keys):
        raise StoreError(
            "CSV 첫 줄에 '설비명'과 'IP' 열이 있어야 합니다.\n"
            "첫 줄 예: " + ",".join(CSV_COLUMNS) + ",비밀번호\n\n"
            "비밀번호만 넣으려면 첫 줄을 '설비명,비밀번호'로 합니다."
        )
    rows = []
    for line_no, cells in enumerate(reader, start=2):
        if not any(c.strip() for c in cells):
            continue
        rows.append((line_no, {k: c for k, c in zip(keys, cells) if k}))
    return rows, "host" in keys


def export_csv(items: list[Equipment], csv_path: str) -> None:
    """엑셀에서 바로 열리도록 BOM 붙은 UTF-8로 쓴다."""
    try:
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(CSV_COLUMNS)
            for e in items:
                w.writerow([e.name, e.group, e.host, e.port, e.os, e.share, e.memo,
                            "Y" if e.share_enabled else "N"])
    except OSError as e:
        raise StoreError(
            f"CSV 파일을 저장할 수 없습니다.\n{csv_path}\n\n"
            f"같은 파일이 엑셀에 열려 있으면 닫고 다시 시도하세요.\n({e})"
        ) from e


# --------------------------------------------------------------- VNC ---

_PF = (r"C:\Program Files", r"C:\Program Files (x86)")

# args: 접속 인자, pw: 비밀번호를 저장해 둔 설비에만 덧붙이는 인자("" = 명령줄 전달 불가)
VIEWERS = {
    "tightvnc": {
        "label": "TightVNC",
        "args": "-host={host} -port={port}",
        "pw": "-password={password}",
        "paths": [p + r"\TightVNC\tvnviewer.exe" for p in _PF],
    },
    "ultravnc": {
        "label": "UltraVNC",
        "args": "-connect {host}::{port}",
        "pw": "-password {password}",
        "paths": [p + r"\uvnc bvba\UltraVNC\vncviewer.exe" for p in _PF],
    },
    "realvnc": {
        "label": "RealVNC Viewer",
        "args": "{host}::{port}",
        "pw": "",
        "paths": [p + r"\RealVNC\VNC Viewer\vncviewer.exe" for p in _PF],
    },
    "tigervnc": {
        "label": "TigerVNC",
        "args": "{host}::{port}",
        "pw": "",
        "paths": [p + r"\TigerVNC\vncviewer.exe" for p in _PF],
    },
    "custom": {"label": "직접 입력", "args": "", "pw": "", "paths": []},
}


def detect_viewer() -> tuple[str, str] | None:
    """설치된 뷰어를 찾아 (종류, 경로)를 돌려준다."""
    for key, v in VIEWERS.items():
        for p in v["paths"]:
            if os.path.isfile(p):
                return key, p
    return None


def _fill(template: str, values: dict) -> list[str]:
    # 빈칸으로 먼저 자른 뒤 채운다 - 비밀번호에 빈칸이 있어도 인자 하나로 남는다.
    out = []
    for token in template.split():
        for k, v in values.items():
            token = token.replace("{" + k + "}", v)
        out.append(token)
    return out


@dataclass
class Config:
    viewer_type: str = "tightvnc"
    viewer_path: str = ""
    extra_args: str = ""
    custom_args: str = "{host}::{port}"
    custom_pw_args: str = ""
    equipment_file: str = ""
    check_on_start: bool = True
    check_timeout: float = 1.5
    show_share: bool = True        # 공유폴더 버튼을 화면에 보일지 (전체)
    title_with_name: bool = True   # VNC 창 제목에 설비명을 붙일지

    def templates(self) -> tuple[str, str]:
        if self.viewer_type == "custom":
            return self.custom_args, self.custom_pw_args
        v = VIEWERS.get(self.viewer_type, VIEWERS["tightvnc"])
        return v["args"], v["pw"]

    def supports_password(self) -> bool:
        return bool(self.templates()[1].strip())


def build_command(cfg: Config, eq: Equipment, password: str = "") -> list[str]:
    """뷰어 실행 인자 목록. 셸을 거치지 않으므로 따옴표 처리가 필요 없다."""
    if not cfg.viewer_path:
        raise StoreError(
            "VNC 뷰어 프로그램 경로가 설정되지 않았습니다.\n"
            "[더보기 > 설정]에서 뷰어 실행 파일(예: tvnviewer.exe)을 지정하세요."
        )
    errors = validate(eq)
    if errors:
        raise StoreError("\n".join(errors))
    args, pw = cfg.templates()
    values = {"host": eq.host, "port": str(eq.port), "name": eq.name}
    cmd = [cfg.viewer_path] + _fill(args, values)
    if password and pw.strip():
        cmd += _fill(pw, {**values, "password": password})
    return cmd + cfg.extra_args.split()


def is_reachable(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# --------------------------------------------------------------- 설정 ---

def app_dir() -> str:
    """exe(또는 실행 스크립트)가 놓인 폴더."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_text(path: str) -> str:
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return raw.decode("utf-8-sig")  # 메모장이 붙인 BOM 포함
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")


def load_config(path: str) -> Config:
    cfg = Config()
    if not os.path.exists(path):
        return cfg
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read_string(_read_text(path))
    except (OSError, configparser.Error) as e:
        raise StoreError(
            f"설정 파일을 읽을 수 없습니다.\n{path}\n\n"
            f"파일을 지우고 다시 실행하면 기본 설정으로 새로 만들어집니다.\n({e})"
        ) from e
    g = lambda s, k, d: cp.get(s, k, fallback=d).strip()
    cfg.viewer_type = g("viewer", "type", cfg.viewer_type).lower()
    if cfg.viewer_type not in VIEWERS:
        cfg.viewer_type = "custom"
    cfg.viewer_path = g("viewer", "path", "").strip('"')
    cfg.extra_args = g("viewer", "extra_args", "")
    cfg.custom_args = g("viewer", "custom_args", cfg.custom_args)
    cfg.custom_pw_args = g("viewer", "custom_password_args", "")
    cfg.equipment_file = g("data", "equipment_file", "").strip('"')
    cfg.check_on_start = _to_bool(g("status", "check_on_start", "yes"))
    cfg.show_share = _to_bool(g("ui", "show_share_button", "yes"))
    cfg.title_with_name = _to_bool(g("viewer", "title_with_name", "yes"))
    try:
        cfg.check_timeout = min(10.0, max(0.3, float(g("status", "timeout_seconds", "1.5"))))
    except ValueError:
        pass
    return cfg


def save_config(cfg: Config, path: str) -> None:
    text = f"""; 설비 원격 접속 설정 파일 (프로그램의 [더보기 > 설정] 화면에서 바꾸는 것을 권장합니다)

[viewer]
; VNC 뷰어 종류: tightvnc / ultravnc / realvnc / tigervnc / custom
type = {cfg.viewer_type}
; 뷰어 실행 파일의 전체 경로
path = {cfg.viewer_path}
; 모든 접속에 덧붙일 추가 옵션 (없으면 비워 둠)
extra_args = {cfg.extra_args}
; type = custom 일 때만 쓰는 인자 형식. {{host}} {{port}} {{password}} 자리에 값이 들어갑니다.
custom_args = {cfg.custom_args}
custom_password_args = {cfg.custom_pw_args}
; VNC 창의 제목 표시줄 앞에 [설비명] 을 붙일지 (yes / no)
title_with_name = {"yes" if cfg.title_with_name else "no"}

[data]
; 설비 목록 파일 위치. 비워 두면 프로그램 폴더의 equipment.json 을 씁니다.
; 여러 PC가 같은 목록을 쓰려면 공유 폴더 경로를 적습니다. 예: \\\\서버\\공유\\equipment.json
equipment_file = {cfg.equipment_file}

[ui]
; 설비 버튼에 공유폴더 아이콘을 보일지 (yes / no). no 로 하면 모든 설비에서 사라집니다.
show_share_button = {"yes" if cfg.show_share else "no"}

[status]
; 프로그램을 켤 때 각 설비의 VNC 포트 응답을 확인할지 (yes / no)
check_on_start = {"yes" if cfg.check_on_start else "no"}
; 설비 한 대의 응답을 기다리는 시간(초)
timeout_seconds = {cfg.check_timeout:g}
"""
    try:
        with open(path, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(text)
    except OSError as e:
        raise StoreError(
            f"설정 파일을 저장할 수 없습니다.\n{path}\n\n"
            f"프로그램을 쓰기 가능한 폴더(예: 바탕 화면, D:\\Tools)로 옮긴 뒤 다시 실행하세요.\n({e})"
        ) from e


def equipment_file(cfg: Config) -> str:
    return cfg.equipment_file or os.path.join(app_dir(), "equipment.json")
