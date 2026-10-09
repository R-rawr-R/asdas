"""Entry point for the BabyCue camera viewer."""

from __future__ import annotations

import argparse
import logging
import sys

from babycue_camera import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="babycue-camera", description="BabyCue phone camera viewer")
    parser.add_argument("--url", help="Stream URL or IP shown by the phone app (pre-fills the address field)")
    parser.add_argument("--connect", action="store_true", help="Connect immediately on start-up")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=args.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    from PySide6.QtWidgets import QApplication

    from babycue_camera.ui import MainWindow

    app = QApplication(sys.argv[:1])
    app.setApplicationName("BabyCue Camera Viewer")
    window = MainWindow()
    if args.url:
        window.url_edit.setText(args.url)
    window.show()
    if args.connect:
        window.connect_stream()
    return app.exec()
