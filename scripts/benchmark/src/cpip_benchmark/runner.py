from __future__ import annotations

import argparse
import json
import os
import runpy
import shutil
import subprocess
import sys
from pathlib import Path


def cleanup(paths: list[str], *, mkdir: list[str]) -> int:
    for value in paths:
        path = Path(value)
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
    for value in mkdir:
        Path(value).mkdir(parents=True, exist_ok=True)
    return 0


def parse_env(items: list[str]) -> dict[str, str]:
    return dict(item.split("=", 1) for item in items)


def run_command(command: list[str], env: dict[str, str]) -> int:
    environment = os.environ.copy()
    environment.update(env)
    completed = subprocess.run(command, env=environment, check=False)
    return completed.returncode


def run_chain(spec: str) -> int:
    """Run a JSON sequence of steps, stopping at the first failure.

    hyperfine takes a single ``--setup``/``--prepare`` command, and with
    ``--shell=none`` there is no shell left to join several of them with
    ``&&``. Preparation is untimed, so running the whole chain in one
    interpreter costs the benchmark nothing.

    Each step is ``{"kind": "cleanup", "path": [...], "mkdir": [...]}`` or
    ``{"kind": "run", "command": [...], "env": {...}}``.
    """
    for step in json.loads(spec):
        kind = step["kind"]
        if kind == "cleanup":
            cleanup(step.get("path", []), mkdir=step.get("mkdir", []))
            continue
        if kind == "run":
            code = run_command(step["command"], step.get("env", {}))
            if code != 0:
                return code
            continue
        raise ValueError(f"Unknown step kind: {kind}")
    return 0


def run_module(module: str, args: list[str], env: list[str]) -> int:
    os.environ.update(parse_env(env))
    sys.argv = [module, *args]
    try:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        if isinstance(exc.code, int):
            return exc.code
        return 1 if exc.code else 0
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--path", action="append", default=[])
    cleanup_parser.add_argument("--mkdir", action="append", default=[])

    chain_parser = subparsers.add_parser("chain")
    chain_parser.add_argument("--spec", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--env", action="append", default=[])
    run_parser.add_argument("args", nargs=argparse.REMAINDER)

    module_parser = subparsers.add_parser("module")
    module_parser.add_argument("--env", action="append", default=[])
    module_parser.add_argument("module")
    module_parser.add_argument("args", nargs=argparse.REMAINDER)

    args = parser.parse_args()
    if args.command == "cleanup":
        return cleanup(args.path, mkdir=args.mkdir)
    if args.command == "chain":
        return run_chain(args.spec)
    if args.command == "run":
        return run_command(args.args, parse_env(args.env))
    if args.command == "module":
        return run_module(args.module, args.args, args.env)
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
