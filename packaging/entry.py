"""打包入口：默认开应用窗口；`--texlate` 转发给引擎 CLI。"""

import multiprocessing
import sys


def _run_texlate() -> None:
    from texlate.cli import app as cli_app

    sys.argv = ["texlate", *sys.argv[2:]]
    cli_app()


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--texlate":
        _run_texlate()
        return
    from app.main import main as app_main

    app_main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
