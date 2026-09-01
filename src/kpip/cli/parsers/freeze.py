"""Argument parser for ``kpip freeze``.

Kept apart from the command module so that ``kpip freeze --help`` builds a
parser without loading the machinery that runs the command.
"""

from __future__ import annotations

from kpip.cli.parser import ArgumentParser


def create_parser() -> ArgumentParser:
    parser = ArgumentParser(prog="kpip freeze")

    parser.add_argument("-r", "--requirement", action="append", default=[])

    parser.add_argument("--all", action="store_true")

    parser.add_argument("--user", action="store_true")

    parser.add_argument("--path", action="append", default=[])

    parser.add_argument("--exclude", action="append", default=[])

    parser.add_argument("--exclude-editable", action="store_true")

    return parser
