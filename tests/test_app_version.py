"""版本号读取测试。"""
from __future__ import annotations

from pathlib import Path

from common.app_version import read_project_version


def test_read_project_version_from_pyproject(tmp_path: Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "triangle-alpha-master"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )

    assert read_project_version(candidate_paths=[pyproject]) == "1.2.3"


def test_read_project_version_falls_back_when_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"

    assert read_project_version("9.9.9", candidate_paths=[missing]) == "9.9.9"


def test_master_spec_bundles_pyproject() -> None:
    spec_text = Path("master.spec").read_text(encoding="utf-8")

    assert "('pyproject.toml', '.')" in spec_text
