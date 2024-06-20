from pathlib import Path

import tomllib

from jira_sync import version

HERE = Path(__file__).parent
PYPROJECT_TOML_PATH = HERE.parent / "pyproject.toml"


def test_version_matches():
    """This checks that pyproject.toml and the package agree on the version."""
    pyproject = tomllib.load(PYPROJECT_TOML_PATH.open("rb"))

    assert version.__version__ == pyproject["tool"]["poetry"]["version"]
