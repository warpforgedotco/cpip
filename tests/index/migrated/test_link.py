from __future__ import annotations

import os
import posixpath
from pathlib import Path

import pytest
from kpip.core.errors import KpipError
from kpip.core.hashes import Hashes
from kpip.index.links import InvalidEggFragment, Link
from kpip.index.paths import PathComponent
from kpip.index.source_models import ArtifactKind


class TestLink:
    @pytest.mark.parametrize(
        "url, expected",
        [
            (
                "https://user:password@example.com/path/page.html",
                "<Link https://user:****@example.com/path/page.html>",
            ),
        ],
    )
    def test_repr(self, url: str, expected: str) -> None:
        link = Link(url)
        assert repr(link) == expected

    def test_from_known_file_skips_directory_probe(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wheel = tmp_path / "demo-1.0-py3-none-any.whl"
        wheel.touch()

        def fail_is_dir(path: Path) -> bool:
            raise AssertionError(f"rechecked known file: {path}")

        monkeypatch.setattr(Path, "is_dir", fail_is_dir)

        link = Link.from_path(wheel, source_url=None, is_dir=False)

        assert link.kind is ArtifactKind.WHEEL

    def test_from_path_retains_known_local_path(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        wheel = tmp_path / "demo-1.0-py3-none-any.whl"
        wheel.touch()

        def fail_url_to_path(url: str) -> str:
            raise AssertionError(f"converted known local URL back to a path: {url}")

        monkeypatch.setattr("kpip.index.links.url_to_path", fail_url_to_path)

        link = Link.from_path(wheel, source_url=None, is_dir=False)

        assert link.file_path == str(wheel)

    @pytest.mark.parametrize(
        "url, expected",
        [
            ("http://yo/wheel.whl", "wheel.whl"),
            ("http://yo/wheel", "wheel"),
            ("https://example.com/path/page.html", "page.html"),
            ("https://example.com/path/page%231.html", "page#1.html"),
            (
                "https://example.com/a%252Fb.whl",
                "a%2Fb.whl",
            ),
            (
                "http://yo/myproject-1.0%2Bfoobar.0-py2.py3-none-any.whl",
                "myproject-1.0+foobar.0-py2.py3-none-any.whl",
            ),
            ("https://example.com/path/", "path"),
            ("https://example.com/path//", "path"),
            ("https://example.com/", "example.com"),
            (
                "https://user:password@example.com/",
                "example.com",
            ),
        ],
    )
    def test_filename(self, url: str, expected: str) -> None:
        link = Link(url)
        assert link.filename == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/a%252Fb.whl",
            "https://example.com/%252e%252e%252fb.whl",
        ],
    )
    def test_filename_decoded_once_stays_single_component(self, url: str) -> None:
        filename = Link(url).filename
        assert not posixpath.isabs(filename)
        assert posixpath.basename(filename) == filename

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/..",
            "https://example.com/.",
            "https://example.com/foo/%2e%2e",
        ],
    )
    def test_filename_parent_reference_falls_back_to_netloc(self, url: str) -> None:
        assert Link(url).filename == "example.com"

    @pytest.mark.parametrize(
        "url",
        [
            "http://..\\..\\..\\evil.whl",
            "http://../",
            "http://..",
        ],
    )
    def test_filename_is_always_a_path_component(self, url: str) -> None:
        name = Link(url).filename
        assert os.path.basename(name) == name
        assert name not in (os.curdir, os.pardir)

    def test_splitext(self) -> None:
        assert Link("http://yo/wheel.whl").splitext() == ("wheel", ".whl")

    def test_no_ext(self) -> None:
        assert Link("http://yo/wheel").ext == ""

    def test_ext(self) -> None:
        assert Link("http://yo/wheel.whl").ext == ".whl"

    def test_ext_fragment(self) -> None:
        assert Link("http://yo/wheel.whl#frag").ext == ".whl"

    def test_ext_query(self) -> None:
        assert Link("http://yo/wheel.whl?a=b").ext == ".whl"

    def test_is_wheel(self) -> None:
        assert Link("http://yo/wheel.whl").is_wheel

    def test_is_wheel_false(self) -> None:
        assert not Link("http://yo/not_a_wheel").is_wheel

    def test_fragments(self) -> None:
        url = "git+https://example.com/package#egg=eggname"
        assert Link(url).egg_fragment == "eggname"
        assert None is Link(url).subdirectory_fragment
        url = "git+https://example.com/package#egg=eggname&subdirectory=subdir"
        assert Link(url).egg_fragment == "eggname"
        assert Link(url).subdirectory_fragment == "subdir"
        url = "git+https://example.com/package#subdirectory=subdir&egg=eggname"
        assert Link(url).egg_fragment == "eggname"
        assert Link(url).subdirectory_fragment == "subdir"

    @pytest.mark.parametrize(
        "fragment",
        [
            "~invalid~package~name~",
            "eggname==1.2.3",
            "eggname>=1.2.3",
            "eggname[!]",
            "eggname[extra]",
            "eggname[extra1,extra2]",
            "eggmame[]",
            "eggname[extra]==1000",
        ],
    )
    def test_invalid_egg_fragments(self, fragment: str) -> None:
        url = f"git+https://example.com/package#egg={fragment}"
        with pytest.raises(KpipError):
            Link(url)

    def test_invalid_egg_fragment_with_extras_and_version_hint(self) -> None:
        """Test that fragments with extras and version specifiers get proper hint."""
        url = "git+https://example.com/package#egg=eggname[extra]==1.0"
        with pytest.raises(InvalidEggFragment) as exc_info:
            Link(url)

        hint = str(exc_info.value.hint_stmt)
        assert r"name\[extra] @ URL" in hint
        assert "Version specifiers are silently ignored" in hint

    @pytest.mark.parametrize(
        "yanked_reason, expected",
        [
            (None, False),
            ("", True),
            ("there was a mistake", True),
        ],
    )
    def test_is_yanked(self, yanked_reason: str | None, expected: bool) -> None:
        link = Link(
            "https://example.com/wheel.whl",
            yanked_reason=yanked_reason,
        )
        assert link.is_yanked == expected

    @pytest.mark.parametrize(
        "hash_name, hex_digest, expected",
        [
            ("sha384", 128 * "a", False),
            ("sha512", 128 * "a", True),
            ("sha512", 128 * "b", True),
            ("sha512", 128 * "c", False),
            ("sha512", "", False),
        ],
    )
    def test_is_hash_allowed(
        self,
        hash_name: str,
        hex_digest: str,
        expected: bool,
    ) -> None:
        url = f"https://example.com/wheel.whl#{hash_name}={hex_digest}"
        link = Link(url)
        hashes_data = {
            "sha512": [128 * "a", 128 * "b"],
        }
        hashes = Hashes(hashes_data)
        assert link.is_hash_allowed(hashes) == expected

    def test_is_hash_allowed__no_hash(self) -> None:
        link = Link("https://example.com/wheel.whl")
        hashes_data = {
            "sha512": [128 * "a"],
        }
        hashes = Hashes(hashes_data)
        assert not link.is_hash_allowed(hashes)

    @pytest.mark.parametrize(
        "hashes, expected",
        [
            (None, False),
            (Hashes({"sha512": [128 * "a"]}), True),
        ],
    )
    def test_is_hash_allowed__none_hashes(
        self,
        hashes: Hashes | None,
        expected: bool,
    ) -> None:
        url = "https://example.com/wheel.whl#sha512={}".format(128 * "a")
        link = Link(url)
        assert link.is_hash_allowed(hashes) == expected

    @pytest.mark.parametrize(
        "url, expected",
        [
            ("git+https://github.com/org/repo", True),
            ("bzr+http://bzr.myproject.org/MyProject/trunk/#egg=MyProject", True),
            ("hg+file://hg.company.com/repo", True),
            ("https://example.com/some.whl", False),
            ("file://home/foo/some.whl", False),
        ],
    )
    def test_is_vcs(self, url: str, expected: bool) -> None:
        link = Link(url)
        assert link.is_vcs is expected


@pytest.mark.parametrize(
    "name",
    [
        "wheel.whl",
        "myproject-1.0+foobar.0-py2.py3-none-any.whl",
        "a%2Fb.whl",
    ],
)
def test_as_path_component_keeps_plain_name(name: str) -> None:
    assert PathComponent.from_name(name, required=True) == name


@pytest.mark.parametrize(
    "name",
    [
        os.path.join(os.sep, "abs", "pkg.whl"),
        os.path.join("..", "pkg.whl"),
        os.path.join("nested", "pkg.whl"),
    ],
)
def test_as_path_component_reduces_to_basename(name: str) -> None:
    assert PathComponent.from_name(name, required=True) == os.path.basename(name)


@pytest.mark.parametrize("name", ["", ".", "..", "/", os.path.join("sub", "..")])
def test_as_path_component_rejects_empty_or_parent_reference(name: str) -> None:
    with pytest.raises(ValueError):
        PathComponent.from_name(name, required=True)


@pytest.mark.parametrize(
    "name",
    [
        "pkg.whl",
        "a%2Fb.whl",
    ],
)
def test_join_within_directory_stays_inside(name: str) -> None:
    directory = os.path.join("base", "downloads")
    joined = PathComponent.from_name(name, required=True).join(directory)
    assert joined == os.path.join(directory, name)
    assert os.path.basename(joined) == name
