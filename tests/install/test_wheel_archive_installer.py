"""Unit tests for src/cpip/install/wheel_archive_installer.py helpers."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cpip.core.errors import InstallationError
from cpip.core.wheel import wheel_candidate
from cpip.install.target import InstallTarget
from cpip.install.wheel_archive_cache import prepare_cached_wheels
from cpip.install.wheel_archive_installer import (
    _rewrite_metadata,
    install_wheels_from_archive_cache,
)


def test_rewrite_metadata_normalizes_mismatched_name(tmp_path: Path) -> None:
    """A wheel's on-disk METADATA Name is normalized to the resolved candidate

    name, even when that name contains characters isalpha() rejects (a
    hyphen, here) -- most real package names do.
    """
    path = tmp_path / "METADATA"
    path.write_text("Metadata-Version: 2.1\nName: Owner_Demo\nVersion: 1.0\n")
    candidate = SimpleNamespace(name="owner-demo")

    rewritten = _rewrite_metadata(str(path), candidate)

    assert rewritten is not None
    assert "Name: owner-demo\n" in path.read_text()


def test_rewrite_metadata_normalizes_digit_containing_name(tmp_path: Path) -> None:
    path = tmp_path / "METADATA"
    path.write_text("Metadata-Version: 2.1\nName: Numpy2\nVersion: 1.0\n")
    candidate = SimpleNamespace(name="numpy2")

    rewritten = _rewrite_metadata(str(path), candidate)

    assert rewritten is not None
    assert "Name: numpy2\n" in path.read_text()


def test_rewrite_metadata_is_noop_when_already_normalized(tmp_path: Path) -> None:
    path = tmp_path / "METADATA"
    original = "Metadata-Version: 2.1\nName: owner-demo\nVersion: 1.0\n"
    path.write_text(original)
    candidate = SimpleNamespace(name="owner-demo")

    rewritten = _rewrite_metadata(str(path), candidate)

    assert rewritten is None
    assert path.read_text() == original


def _make_wheel(
    directory: Path,
    name: str,
    *,
    shared_module: str | None = None,
    entry_points: str | None = None,
) -> Path:
    """Build a minimal wheel, optionally with a file at ``shared_module`` or

    an ``entry_points.txt``. Two wheels sharing a ``shared_module`` path or a
    console-script name simulate a colliding destination -- both would try
    to write the same file into the target.
    """
    wheel = directory / f"{name}-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        if shared_module is not None:
            archive.writestr(shared_module, f"# from {name}\n")
        archive.writestr(
            f"{name}-1.0.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n",
        )
        archive.writestr(
            f"{name}-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        if entry_points is not None:
            archive.writestr(f"{name}-1.0.dist-info/entry_points.txt", entry_points)
        archive.writestr(f"{name}-1.0.dist-info/RECORD", "")
    return wheel


def _prevalidated_candidates(
    tmp_path: Path,
    cache_dir: Path,
    wheel_a: Path,
    wheel_b: Path,
) -> tuple[object, object]:
    """Build two candidates with ``wheel_layout`` already set to a

    CachedWheelArchive, matching what the normal candidate-materialization
    path (``install/output.py:prepare_install_candidates``) already does
    before ``install_wheels_from_archive_cache`` ever runs.
    """
    candidate_a = wheel_candidate(wheel_a).copy_with(
        source_hashes={"sha256": hashlib.sha256(wheel_a.read_bytes()).hexdigest()},
        source_kind="wheel",
    )
    candidate_b = wheel_candidate(wheel_b).copy_with(
        source_hashes={"sha256": hashlib.sha256(wheel_b.read_bytes()).hexdigest()},
        source_kind="wheel",
    )

    archives = prepare_cached_wheels((candidate_a, candidate_b), str(cache_dir))

    return (
        candidate_a.copy_with(wheel_layout=archives[0]),
        candidate_b.copy_with(wheel_layout=archives[1]),
    )


def test_prevalidated_candidates_still_reject_colliding_files(
    tmp_path: Path,
) -> None:
    cache_dir = tmp_path / "cache"
    wheel_a = _make_wheel(tmp_path, "pkg_a", shared_module="shared_thing.py")
    wheel_b = _make_wheel(tmp_path, "pkg_b", shared_module="shared_thing.py")
    candidate_a, candidate_b = _prevalidated_candidates(
        tmp_path,
        cache_dir,
        wheel_a,
        wheel_b,
    )

    target = tmp_path / "target"
    install_target = InstallTarget.from_options("pkg_a", target=str(target))

    with pytest.raises(InstallationError, match="duplicate installation destination"):
        install_wheels_from_archive_cache(
            [(wheel_a, True, None), (wheel_b, True, None)],
            (candidate_a, candidate_b),
            target=install_target,
            cache_dir=str(cache_dir),
        )


def test_prevalidated_candidates_still_reject_colliding_console_scripts(
    tmp_path: Path,
) -> None:
    """Two unrelated packages both providing a ``mytool`` console script

    must fail the batch, not silently install one script and drop the
    other. Script generation writes via ``os.rename``, which overwrites
    silently on POSIX -- the batch-level destination reservation
    (``_reserve_destination``) is the only thing that can catch this, and it
    must run unconditionally.
    """
    cache_dir = tmp_path / "cache"
    entry_points = "[console_scripts]\nmytool = pkg:main\n"
    wheel_a = _make_wheel(tmp_path, "pkg_a", entry_points=entry_points)
    wheel_b = _make_wheel(tmp_path, "pkg_b", entry_points=entry_points)
    candidate_a, candidate_b = _prevalidated_candidates(
        tmp_path,
        cache_dir,
        wheel_a,
        wheel_b,
    )

    target = tmp_path / "target"
    install_target = InstallTarget.from_options("pkg_a", target=str(target))

    with pytest.raises(InstallationError, match="duplicate installation destination"):
        install_wheels_from_archive_cache(
            [(wheel_a, True, None), (wheel_b, True, None)],
            (candidate_a, candidate_b),
            target=install_target,
            cache_dir=str(cache_dir),
        )


def test_record_rows_match_the_files_on_disk(tmp_path: Path) -> None:
    """Rows whose hash is computed from the bytes written (INSTALLER,
    REQUESTED, a rewritten METADATA) must agree with the files themselves."""
    import base64
    import csv

    cache_dir = tmp_path / "cache"
    wheel = _make_wheel(tmp_path, "Mixed_Case", shared_module="mixed_case.py")
    (candidate,) = _prevalidated_candidates_for(tmp_path, cache_dir, wheel)
    target = tmp_path / "target"
    install_target = InstallTarget.from_options("mixed-case", target=str(target))

    installed = install_wheels_from_archive_cache(
        [(wheel, True, None)],
        (candidate,),
        target=install_target,
        cache_dir=str(cache_dir),
    )
    assert installed is not None

    dist_info = next(target.glob("*.dist-info"))
    rows = list(csv.reader((dist_info / "RECORD").read_text().splitlines()))
    checked = 0
    for relative, digest, size in rows:
        if not digest:
            continue
        path = target / relative
        data = path.read_bytes()
        expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
        assert digest == f"sha256={expected.rstrip(b'=').decode('ascii')}", relative
        assert size == str(len(data)), relative
        checked += 1
    assert {row[0].rsplit("/", 1)[-1] for row in rows} >= {
        "INSTALLER",
        "REQUESTED",
        "METADATA",
        "RECORD",
    }
    assert checked >= 3


def _prevalidated_candidates_for(
    tmp_path: Path,
    cache_dir: Path,
    *wheels: Path,
) -> tuple[object, ...]:
    candidates = tuple(
        wheel_candidate(wheel).copy_with(
            source_hashes={"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
            source_kind="wheel",
        )
        for wheel in wheels
    )
    archives = prepare_cached_wheels(candidates, str(cache_dir))
    return tuple(
        candidate.copy_with(wheel_layout=archive)
        for candidate, archive in zip(candidates, archives, strict=True)
    )


def test_archive_route_compiles_bytecode_and_records_it(tmp_path: Path) -> None:
    """With compilation on, the clone route writes the .pyc files and lists
    them in RECORD with real hashes, like the transactional route does."""
    import base64
    import csv

    cache_dir = tmp_path / "cache"
    wheel = _make_wheel(tmp_path, "pkg_compiled", shared_module="compiled_mod.py")
    (candidate,) = _prevalidated_candidates_for(tmp_path, cache_dir, wheel)
    target = tmp_path / "target"
    install_target = InstallTarget.from_options("pkg-compiled", target=str(target))

    installed = install_wheels_from_archive_cache(
        [(wheel, True, None)],
        (candidate,),
        target=install_target,
        cache_dir=str(cache_dir),
        pycompile=True,
    )
    assert installed is not None

    compiled = sorted(p.relative_to(target).as_posix() for p in target.rglob("*.pyc"))
    assert compiled, "no bytecode was written"
    assert all("__pycache__/" in path for path in compiled)

    dist_info = next(target.glob("*.dist-info"))
    rows = {
        row[0]: row[1:]
        for row in csv.reader((dist_info / "RECORD").read_text().splitlines())
    }
    for path in compiled:
        digest, size = rows[path]
        data = (target / path).read_bytes()
        expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest())
        assert digest == f"sha256={expected.rstrip(b'=').decode('ascii')}"
        assert size == str(len(data))


def test_transactional_install_with_compilation_takes_the_clone_route(
    tmp_path: Path,
) -> None:
    from cpip.install.wheel_archive_cache import ARCHIVE_CACHE_BUCKET
    from cpip.install.wheel_transaction import install_wheels_transactionally

    cache_dir = tmp_path / "cache"
    wheel = _make_wheel(tmp_path, "pkg_default", shared_module="default_mod.py")
    candidate = wheel_candidate(wheel).copy_with(
        source_hashes={"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
        source_kind="wheel",
    )
    target = tmp_path / "target"

    install_wheels_transactionally(
        [(wheel, True, None)],
        target=InstallTarget.from_options("pkg-default", target=str(target)),
        pycompile=True,
        lookup_existing=False,
        candidates=[candidate],
        cache_dir=str(cache_dir),
    )

    assert (cache_dir / ARCHIVE_CACHE_BUCKET).is_dir(), "the clone route was not used"
    assert list(target.rglob("*.pyc")), "bytecode was not compiled"


def _make_wheel_with_members(
    directory: Path, name: str, members: dict[str, str]
) -> Path:
    """A wheel carrying arbitrary members besides its dist-info scaffolding."""
    wheel = directory / f"{name}-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path, text in members.items():
            archive.writestr(path, text)
        archive.writestr(
            f"{name}-1.0.dist-info/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n",
        )
        archive.writestr(
            f"{name}-1.0.dist-info/WHEEL",
            "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{name}-1.0.dist-info/RECORD", "")
    return wheel


def test_wheel_shipping_its_own_generated_pyc_is_rejected_under_compilation(
    tmp_path: Path,
) -> None:
    """A wheel that ships both a module and the .pyc byte-compilation would
    generate collides on one destination; the clone route rejects it rather
    than overwrite the member and emit a duplicate RECORD row."""
    import sys

    cache_dir = tmp_path / "cache"
    tag = sys.implementation.cache_tag
    wheel = _make_wheel_with_members(
        tmp_path,
        "pkg_self_pyc",
        {"selfmod.py": "x = 1\n", f"__pycache__/selfmod.{tag}.pyc": "stale\n"},
    )
    (candidate,) = _prevalidated_candidates_for(tmp_path, cache_dir, wheel)
    install_target = InstallTarget.from_options(
        "pkg-self-pyc", target=str(tmp_path / "target")
    )

    with pytest.raises(InstallationError, match="duplicate installation destination"):
        install_wheels_from_archive_cache(
            [(wheel, True, None)],
            (candidate,),
            target=install_target,
            cache_dir=str(cache_dir),
            pycompile=True,
        )

    installed = install_wheels_from_archive_cache(
        [(wheel, True, None)],
        (candidate,),
        target=InstallTarget.from_options("pkg-self-pyc", target=str(tmp_path / "t2")),
        cache_dir=str(cache_dir),
        pycompile=False,
    )
    assert installed is not None


def test_unowned_target_pyc_declines_the_clone_route(tmp_path: Path) -> None:
    """An unowned file already at a generated .pyc path makes the route
    decline (to the transactional path) instead of overwriting it in the
    clone after preflight."""
    import sys

    cache_dir = tmp_path / "cache"
    tag = sys.implementation.cache_tag
    wheel = _make_wheel_with_members(tmp_path, "pkg_new", {"newmod.py": "x = 1\n"})
    (candidate,) = _prevalidated_candidates_for(tmp_path, cache_dir, wheel)

    target = tmp_path / "target"
    (target / "other-9.9.dist-info").mkdir(parents=True)
    (target / "other-9.9.dist-info" / "RECORD").write_text("")
    stray = target / "__pycache__" / f"newmod.{tag}.pyc"
    stray.parent.mkdir(parents=True)
    stray.write_text("not ours\n")

    install_target = InstallTarget.from_options("pkg-new", target=str(target))

    declined = install_wheels_from_archive_cache(
        [(wheel, True, None)],
        (candidate,),
        target=install_target,
        cache_dir=str(cache_dir),
        pycompile=True,
    )
    assert declined is None
    assert stray.read_text() == "not ours\n"


def _install_one(
    tmp_path: Path,
    wheel: Path,
    name: str,
    *,
    pycompile: bool = True,
) -> tuple[Path, Path]:
    """Install ``wheel`` through the clone route; return (target, cache_dir)."""
    from cpip.install.wheel_transaction import install_wheels_transactionally

    cache_dir = tmp_path / "cache"
    target = tmp_path / "target"
    candidate = wheel_candidate(wheel).copy_with(
        source_hashes={"sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()},
        source_kind="wheel",
    )

    install_wheels_transactionally(
        [(wheel, True, None)],
        target=InstallTarget.from_options(name, target=str(target)),
        pycompile=pycompile,
        lookup_existing=False,
        candidates=[candidate],
        cache_dir=str(cache_dir),
    )

    return target, cache_dir


def _loaded_code(pyc: Path) -> object:
    import marshal

    return marshal.loads(pyc.read_bytes()[16:])


def test_archive_cache_byte_compiles_at_fill_time(tmp_path: Path) -> None:
    """The cache entry carries its own ``pyc/`` tree, laid out by mapped path."""
    from cpip.install.wheel_archive_cache import (
        ARCHIVE_CACHE_BUCKET,
        PYC_CACHE_SUBDIR,
    )

    wheel = _make_wheel_with_members(tmp_path, "fillpkg", {"fillpkg/mod.py": "X = 1\n"})
    _, cache_dir = _install_one(tmp_path, wheel, "fillpkg")

    cached = list((cache_dir / ARCHIVE_CACHE_BUCKET).rglob(f"{PYC_CACHE_SUBDIR}/*"))

    assert cached, "the archive cache did not byte-compile at fill time"
    assert list((cache_dir / ARCHIVE_CACHE_BUCKET).rglob("fillpkg/__pycache__/*.pyc"))


def test_installed_pyc_names_the_installed_path_not_the_staging_directory(
    tmp_path: Path,
) -> None:
    """``co_filename`` must be where the module actually lives.

    The staging directory is renamed away at the end of the install, so a
    ``.pyc`` naming it leaves every traceback from that module without source.
    """
    wheel = _make_wheel_with_members(
        tmp_path,
        "namepkg",
        {"namepkg/mod.py": "def f():\n    return 1\n"},
    )
    target, _ = _install_one(tmp_path, wheel, "namepkg")

    pyc = next((target / "namepkg" / "__pycache__").glob("mod.*.pyc"))
    code = _loaded_code(pyc)

    assert code.co_filename == str(target / "namepkg" / "mod.py")
    assert Path(code.co_filename).is_file()


def test_installed_pyc_rebinds_nested_code_objects(tmp_path: Path) -> None:
    """Functions and classes carry their own code objects; all must be rebound."""
    from types import CodeType

    wheel = _make_wheel_with_members(
        tmp_path,
        "nestpkg",
        {
            "nestpkg/mod.py": (
                "class C:\n"
                "    def method(self):\n"
                "        def inner():\n"
                "            return 3\n"
                "        return inner\n"
            ),
        },
    )
    target, _ = _install_one(tmp_path, wheel, "nestpkg")

    pyc = next((target / "nestpkg" / "__pycache__").glob("mod.*.pyc"))
    expected = str(target / "nestpkg" / "mod.py")

    seen = 0

    def walk(code: CodeType) -> None:
        nonlocal seen
        seen += 1
        assert code.co_filename == expected
        for const in code.co_consts:
            if isinstance(const, CodeType):
                walk(const)

    walk(_loaded_code(pyc))

    assert seen >= 4, "expected module, class body, method and closure"


def test_installed_pyc_is_not_stale_for_the_interpreter(tmp_path: Path) -> None:
    """The header must validate against the installed source, or every import
    silently recompiles and the cached bytecode buys nothing."""
    import importlib.util

    wheel = _make_wheel_with_members(
        tmp_path,
        "freshpkg",
        {"freshpkg/mod.py": "VALUE = 42\n"},
    )
    target, _ = _install_one(tmp_path, wheel, "freshpkg")

    source = target / "freshpkg" / "mod.py"
    pyc = Path(importlib.util.cache_from_source(str(source)))
    header = pyc.read_bytes()[:16]
    stat = source.stat()

    assert header[:4] == importlib.util.MAGIC_NUMBER
    assert int.from_bytes(header[4:8], "little") == 0, "expected timestamp mode"
    assert int.from_bytes(header[8:12], "little") == int(stat.st_mtime) & 0xFFFFFFFF
    assert int.from_bytes(header[12:16], "little") == stat.st_size & 0xFFFFFFFF


def test_data_purelib_modules_are_compiled_at_their_relocated_path(
    tmp_path: Path,
) -> None:
    """``.data/purelib`` members move to the target root during install; their
    bytecode has to land beside them, not beside the pre-relocation path."""
    wheel = _make_wheel_with_members(
        tmp_path,
        "datapkg",
        {"datapkg-1.0.data/purelib/datapkg/mod.py": "Y = 2\n"},
    )
    target, _ = _install_one(tmp_path, wheel, "datapkg")

    pyc = next((target / "datapkg" / "__pycache__").glob("mod.*.pyc"))

    assert _loaded_code(pyc).co_filename == str(target / "datapkg" / "mod.py")
    assert not (target / "datapkg-1.0.data").exists()


def test_a_module_that_cannot_compile_does_not_fail_the_install(
    tmp_path: Path,
) -> None:
    """Wheels ship unbuildable Python (vendored Python 2, most often). The
    module still installs; only its bytecode is missing."""
    wheel = _make_wheel_with_members(
        tmp_path,
        "badpkg",
        {
            "badpkg/good.py": "OK = 1\n",
            "badpkg/broken.py": "print 'python 2'\n",
        },
    )
    target, _ = _install_one(tmp_path, wheel, "badpkg")

    assert (target / "badpkg" / "broken.py").is_file()
    assert list((target / "badpkg" / "__pycache__").glob("good.*.pyc"))
    assert not list((target / "badpkg" / "__pycache__").glob("broken.*.pyc"))


def test_no_compile_installs_no_bytecode(tmp_path: Path) -> None:
    wheel = _make_wheel_with_members(
        tmp_path,
        "plainpkg",
        {"plainpkg/mod.py": "Z = 3\n"},
    )
    target, _ = _install_one(tmp_path, wheel, "plainpkg", pycompile=False)

    assert (target / "plainpkg" / "mod.py").is_file()
    assert not list(target.rglob("*.pyc"))


def test_install_falls_back_when_the_cache_has_no_bytecode(tmp_path: Path) -> None:
    """Entries written before the cache learned to compile have no ``pyc/``.
    They must still install with bytecode, compiled in the stage."""
    import shutil as _shutil

    from cpip.install.wheel_archive_cache import (
        ARCHIVE_CACHE_BUCKET,
        PYC_CACHE_SUBDIR,
    )

    wheel = _make_wheel_with_members(
        tmp_path,
        "oldpkg",
        {"oldpkg/mod.py": "W = 4\n"},
    )
    target, cache_dir = _install_one(tmp_path, wheel, "oldpkg")
    _shutil.rmtree(target)

    for stale in (cache_dir / ARCHIVE_CACHE_BUCKET).rglob(PYC_CACHE_SUBDIR):
        _shutil.rmtree(stale)

    target, _ = _install_one(tmp_path, wheel, "oldpkg")

    assert list((target / "oldpkg" / "__pycache__").glob("mod.*.pyc"))
