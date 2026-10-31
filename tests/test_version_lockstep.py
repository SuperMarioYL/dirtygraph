"""Version-lockstep guard — every shipped version surface must agree.

The repo carries the release version in several places: the ``VERSION`` file, the
package constant ``dirtygraph.__version__`` (which ``pyproject.toml``'s
``[tool.hatch.version]`` derives), ``pyproject.toml``'s own ``project.version``,
the head ``## [x.y.z]`` section of ``CHANGELOG.md``, and the site's
product-version key ``meta.content_version`` in ``web/site.json``. Nothing else
keeps them in step, so this test fails the moment one surface is bumped alone
(the classic drift: tag cut from one surface while the rest lag behind).

The site-schema key (``web/site.json`` ``schema``) is intentionally NOT part of
this contract — it is the site generator's own format version, not the product's.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _file_version() -> str:
    return (ROOT / "VERSION").read_text(encoding="utf-8").strip()


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "pyproject.toml has no version = \"x.y.z\""
    return match.group(1)


def _changelog_head_version() -> str:
    for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines():
        match = re.match(r"^## \[(\d+\.\d+\.\d+)\]", line)
        if match:
            return match.group(1)
    raise AssertionError("CHANGELOG.md has no '## [x.y.z]' section")


def _site_content_version() -> str:
    data = json.loads((ROOT / "web" / "site.json").read_text(encoding="utf-8"))
    version = data.get("meta", {}).get("content_version")
    assert version, "web/site.json meta.content_version is missing"
    return str(version)


def test_version_surfaces_agree():
    """VERSION, __version__, pyproject, CHANGELOG head and site content_version
    must all carry the same release version."""
    from dirtygraph import __version__

    surfaces = {
        "VERSION": _file_version(),
        "dirtygraph.__version__": __version__,
        "pyproject.toml version": _pyproject_version(),
        "CHANGELOG head section": _changelog_head_version(),
        "web/site.json meta.content_version": _site_content_version(),
    }
    assert len(set(surfaces.values())) == 1, f"version drift across surfaces: {surfaces}"


def test_cli_version_command_prints_file_version():
    """`dirtygraph version` prints the package constant, which must equal the
    VERSION file (the release tag is cut from it)."""
    from typer.testing import CliRunner

    import dirtygraph.cli as cli_mod

    res = CliRunner().invoke(cli_mod.app, ["version"])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == _file_version()
