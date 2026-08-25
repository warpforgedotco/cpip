from __future__ import annotations

from pathlib import Path

from cpip.cli import install, main


def test_layered_cli_uses_public_cpip_install_services() -> None:
    source = Path(install.__file__).read_text()
    assert "from cpip.install.metadata import (" in source
    assert "prepare_editable_source," in source
    assert "def _prepare_editable_source" not in source


def test_layered_cli_uninstall_dispatches_to_cpip_install(monkeypatch, capsys) -> None:
    removed: list[str] = []

    def fake_uninstall(name: str) -> bool:
        removed.append(name)
        return True

    from cpip.install.requirements import RequirementInstaller

    monkeypatch.setattr(
        RequirementInstaller,
        "uninstall",
        lambda self, name: fake_uninstall(name),
    )

    assert main.main(["uninstall", "demo-pkg"]) == 0
    assert removed == ["demo-pkg"]
    assert "Successfully uninstalled demo-pkg" in capsys.readouterr().out
