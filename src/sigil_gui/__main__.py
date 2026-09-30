from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="sigil-gui", description="GUI for editing sigil-cli command trees")
    ap.add_argument("project", nargs="?", help="folder containing manifest.yml")
    ap.add_argument("--library", help="fragment library directory (default: per-user data dir or $SIGIL_GUI_LIBRARY)")
    ap.add_argument("--native", action="store_true", help="open in a desktop window (needs nicegui[native])")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8480)
    args = ap.parse_args(argv)

    from nicegui import ui

    from .ui.studio import Studio

    studio = Studio(args.project, args.library)

    @ui.page("/")
    def index() -> None:
        studio.build()

    ui.run(title="Sigil Studio", host=args.host, port=args.port, native=args.native,
           reload=False, show=not args.native)


if __name__ in {"__main__", "__mp_main__"}:
    main()
