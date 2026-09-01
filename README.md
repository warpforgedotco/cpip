# kpip

[![Checks](https://github.com/warpforgedotco/kpip/actions/workflows/checks.yml/badge.svg)](https://github.com/warpforgedotco/kpip/actions/workflows/checks.yml)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE.txt)

**A working reflection of what pip can be when performance engineering is a
design constraint.**

kpip is a working, performance-engineered interpretation of pip. It brings
together concrete improvements across startup, resolution, caching, artifact
handling, and installation into a familiar command model that can be measured,
tested, and evaluated as a complete system.

The goal is not to establish another permanent package-manager ecosystem. All
work here is intended to inform, adapt into, and ultimately flow upstream to
pip.

> [!WARNING]
> kpip is an early-alpha experimental implementation, published on PyPI for
> testing and evaluation. It is not a supported pip distribution or a drop-in
> replacement and should not be used to manage critical or system Python
> environments. Interfaces, behavior, and cache formats may change.

## Why kpip exists

[pip](https://pip.pypa.io/) established the package-installation workflow that
Python users know, and it must evolve while serving an enormous compatibility
surface. [uv](https://github.com/astral-sh/uv) demonstrated how much performance
headroom exists when packaging is reconsidered end to end.

kpip creates room to explore that headroom in a Python-native implementation,
while keeping pip's users, semantics, and upstream constraints in view. It asks
what pip can become when startup, resolution, artifact handling, caching, and
installation are treated as first-class performance problems.

## Upstream is the destination

kpip now contains a working body of performance engineering intended to flow
back to pip and the wider Python packaging ecosystem. The current goal is to
determine how best to upstream that work: identify the improvements that
transfer cleanly, separate them into focused and reviewable changes, adapt them
to pip's architecture and compatibility requirements, and validate them in
pip's own test and benchmark environments.

The repository is not expected to map commit-for-commit onto pip. Its
implementation combines several architectural changes and, in places, narrows
compatibility to make performance gains measurable. Upstreaming means
extracting the underlying ideas and evidence, then reshaping them into changes
that fit pip's maintenance and compatibility constraints.

Each upstream proposal should carry forward:

- a reproducible measurement of the problem and the improvement;
- behavioral and compatibility tests that preserve pip's contract;
- the smallest maintainable implementation that can be proposed upstream; and
- a clear account of tradeoffs, limitations, and results that did not hold up.

Negative results matter too. If an optimization does not survive realistic
workloads or cannot preserve behavior, the useful outcome is the evidence—not a
performance claim.

## Installation

Install kpip from PyPI as an isolated tool with uv:

```console
uv tool install kpip
kpip --version
```

Or run it from a source checkout:

```console
git clone https://github.com/warpforgedotco/kpip.git
cd kpip
uv sync --locked
uv run kpip --version
```

## Quick start

Create an environment, then point an isolated kpip installation at it with the
global `--python` option:

```console
python -m venv .venv
kpip --python .venv install httpx
kpip --python .venv list
```

Install a requirements file:

```console
kpip --python .venv install -r requirements.txt
```

Resolve an input file into `pylock.toml`, then install it:

```console
kpip lock -r requirements.in
kpip --python .venv install -r pylock.toml
```

If kpip is installed inside the environment it should manage, omit
`--python .venv` and invoke `kpip` directly.

## Commands

| Task | Commands |
| --- | --- |
| Install or prepare packages | `install`, `wheel`, `download` |
| Remove packages | `uninstall` |
| Inspect an environment | `list`, `freeze`, `show`, `inspect`, `check` |
| Resolve reproducibly | `lock` |
| Work with indexes and artifacts | `index`, `hash` |
| Inspect or clear local state | `cache` |

Run `kpip <command> --help` for command-specific options.

## Benchmarking

The benchmark suite uses [Hyperfine](https://github.com/sharkdp/hyperfine) to
compare kpip and uv through the same inputs and isolated targets. uv serves as
an external performance reference; before-and-after kpip runs show whether a
specific experiment helped. Neither replaces measuring an eventual patch in
pip's own architecture and test environment. The default offline workload is
generated locally and avoids network variance.

With `hyperfine` and `uv` available on `PATH`:

```console
cd scripts/benchmark
uv sync --locked --group tests
uv run kpip-bench --workload offline
```

The harness includes startup, cold and warm locking, cold and warm
installation, and incremental installation cases. It can also run the
workloads used by uv's public benchmarks, but those are opt-in because live
indexes and platform-specific wheels make them less reproducible.

See the [benchmark guide](scripts/benchmark/README.md) for workload selection,
recording quiet-machine baselines, exporting raw Hyperfine results, and
comparing two commits. The measurement philosophy follows the
[X-Ray Performance Laboratory](https://github.com/KRRT7/xray): benchmark first,
then optimize what the evidence identifies.

## Design

The main path is intentionally layered:

```text
CLI -> resolution -> candidate discovery -> artifact preparation -> transaction
```

Each layer owns one part of the package-installation process. Fast paths are
narrow recognizers that decline to the general implementation whenever they
cannot preserve the same semantics. Persistent caches are optional: a missing,
stale, or corrupt entry must become a cache miss rather than a correctness
failure.

The [architecture guide](docs/architecture.md) maps these boundaries, the
runtime dependency rules between packages, the resolver flow, and every
persistent cache.

## Development

Set up the test and typing environments:

```console
git clone https://github.com/warpforgedotco/kpip.git
cd kpip
uv sync --locked --group test --group typing
```

Run the main local checks:

```console
uv run ruff check src tests conftest.py
uv run ruff format --check src tests conftest.py
uv run ty check src
uv run pytest tests \
  --ignore=tests/cli/functional \
  --ignore=tests/benchmarks \
  -m "not network"
```

Functional tests exercise the real CLI in subprocesses:

```console
uv run pytest tests/cli/functional -n auto
```

The [checks workflow](.github/workflows/checks.yml) is the source of truth for
the supported CI matrix. Before proposing a performance change, record a
comparable before-and-after benchmark; a locally faster microbenchmark is not
enough on its own. A change is not finished merely because it lands in kpip:
identify how its implementation, tests, and evidence can move upstream.

## Acknowledgements

kpip exists because of—and in service of—the interfaces, behavior, and testing
knowledge developed by [pip and PyPA](https://github.com/pypa/pip). It is meant
to help that work move forward, not to pull users or contributors into a
permanently separate ecosystem. Its performance experiments also learn from
the techniques and public workloads in [uv](https://github.com/astral-sh/uv).
Third-party code shipped with kpip is documented in the [vendoring
manifest](src/kpip/_vendor/VENDORED.md).

## License

kpip is available under the [MIT License](LICENSE.txt).
