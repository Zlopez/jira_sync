# Test fixture library
# https://docs.pytest.org/en/7.1.x/reference/fixtures.html#conftest-py-sharing-fixtures-across-multiple-files


from functools import partial
from pathlib import Path
from unittest import mock

import pytest

from jira_sync.config import Config

from .common import (
    gen_test_config,
    mock_jira__create_issue,
    mock_jira__get_issues_by_labels,
)


@pytest.fixture
def test_jira_obj():
    jira = mock.Mock()
    jira.create_issue.side_effect = mock.Mock(wraps=partial(mock_jira__create_issue, {}))
    jira.get_issues_by_labels.side_effect = mock.Mock(wraps=mock_jira__get_issues_by_labels)
    return jira


@pytest.fixture
def test_config(request: pytest.FixtureRequest) -> Config:
    params = {"instances_enabled": True, "repositories_enabled": True}
    if hasattr(request, "param"):
        params |= request.param

    return Config.model_validate(gen_test_config(**params) | {"config_path": Path("/dev/null")})
