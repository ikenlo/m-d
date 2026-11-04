"""Версія узгоджена: код, pyproject і CHANGELOG (з нього GitHub Actions бере опис релізу)."""

import re
from pathlib import Path

from mds import __version__

ROOT = Path(__file__).resolve().parents[2]


def test_version_consistent_and_documented():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), "версія у форматі X.Y.Z"
    pyproject = (ROOT / "server" / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject, "версія в pyproject.toml має збігатися з mds.__version__"
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    first = re.search(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.M)
    assert first and first.group(1) == __version__, "у CHANGELOG.md першим має бути розділ поточної версії"
