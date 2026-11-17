"""설비 원격 접속 - 실행 진입점."""
import os
import sys
import traceback


def _report(text: str) -> None:
    # 콘솔 없는 exe는 print가 보이지 않는다 - 파일과 메시지 창으로 알린다.
    try:
        from launcher.core import app_dir
        base = app_dir()
    except Exception:
        base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    path = os.path.join(base, "실행오류_안내.txt")
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("프로그램이 시작되지 못했습니다. 이 파일을 담당자에게 전달해 주세요.\n\n" + text)
    except OSError:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f"프로그램을 시작하지 못했습니다.\n아래 파일을 담당자에게 전달해 주세요.\n{path}\n\n"
                  + text[-600:], "설비 원격 접속", 0x10)
    else:
        sys.stderr.write(text)


def _tell(text: str) -> None:
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, "설비 원격 접속", 0x40)
    else:
        print(text)


def reset_pin() -> None:
    """EquipmentRemote.exe --reset-pin : 편집 잠금 PIN 을 잊었을 때 잠금을 끈다.

    설비 목록은 건드리지 않고 잠금 정보만 지운다(지우기 전 상태는 backup 폴더에 남는다).
    """
    from launcher import core
    cfg = core.load_config(os.path.join(core.app_dir(), "config.ini"))
    store = core.EquipmentStore(core.equipment_file(cfg))
    try:
        store.load()
        if not store.lock:
            _tell(f"편집 잠금이 걸려 있지 않습니다.\n{store.path}")
            return
        store.set_lock(None)
    except core.StoreError as e:
        _tell(str(e))
        return
    _tell(f"편집 잠금을 껐습니다. 프로그램을 다시 실행해 [더보기 > 편집 잠금 설정]에서 새 PIN을 정하세요.\n{store.path}")


if __name__ == "__main__":
    try:
        if "--reset-pin" in sys.argv[1:]:
            reset_pin()
            sys.exit(0)
        from launcher.ui import main
        main()
    except Exception:
        _report(traceback.format_exc())
        sys.exit(1)
