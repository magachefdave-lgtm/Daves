"""설비 원격 접속 - 실행 진입점."""
import os
import sys
import traceback


def _report(text: str) -> None:
    # 콘솔 없는 exe는 print가 보이지 않는다 - 파일과 메시지 창으로 알린다.
    base = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    try:
        with open(os.path.join(base, "실행오류_안내.txt"), "w", encoding="utf-8-sig") as f:
            f.write("프로그램이 시작되지 못했습니다. 이 파일을 담당자에게 전달해 주세요.\n\n" + text)
    except OSError:
        pass
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, "프로그램을 시작하지 못했습니다.\n프로그램 폴더의 '실행오류_안내.txt'를 "
                  "담당자에게 전달해 주세요.\n\n" + text[-600:], "설비 원격 접속", 0x10)
    else:
        sys.stderr.write(text)


if __name__ == "__main__":
    try:
        from launcher.ui import main
        main()
    except Exception:
        _report(traceback.format_exc())
        sys.exit(1)
