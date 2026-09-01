"""Argument parser for ``kpip uninstall``.

Kept apart from the command module so that ``kpip uninstall --help`` builds a
parser without loading the machinery that runs the command.
"""

from __future__ import annotations

from kpip.cli.parser import ArgumentParser


def create_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="kpip uninstall")

    parser.add_argument("packages", nargs="*")

    parser.add_argument(
        "-r",
        "--requirement",
        dest="requirement_files",
        action="append",
        default=[],
    )

    parser.add_argument("-v", "--verbose", action="count", default=0)

    parser.add_argument("-y", "--yes", action="store_true")

    return parser
