import logging
from functools import partial
from unittest import mock

import pytest
import tomlkit
from click.testing import CliRunner
from jira.exceptions import JIRAError

from jira_sync import main, repositories, sync_mgr
from jira_sync.config.model import JiraConfig
from jira_sync.jira_wrapper import JiraRunMode

from .common import (
    TEST_GITHUB_ISSUES,
    TEST_GITHUB_JIRA_ISSUES,
    TEST_GITHUB_REPOS,
    TEST_JIRA_ISSUES,
    TEST_PAGURE_ISSUES,
    TEST_PAGURE_JIRA_ISSUES,
    TEST_PAGURE_REPOS,
    JiraIssue,
    gen_test_config,
    mock_jira__create_issue,
    mock_jira__get_issues_by_labels,
    mock_requests_get,
)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@main.cli.command(name="test", hidden=True)
def _test_command():
    pass


@pytest.mark.parametrize("verbose", (False, True), ids=("quiet", "verbose"))
def test_cli(verbose, runner):
    cmd_args = ["test"]

    if verbose:
        cmd_args.insert(0, "--verbose")
        level = logging.DEBUG
    else:
        level = logging.INFO

    with mock.patch.object(main.logging, "basicConfig") as basicConfig:
        result = runner.invoke(main.cli, cmd_args, catch_exceptions=False)

    assert result.exit_code == 0

    basicConfig.assert_called_once_with(format=mock.ANY, level=level)


@pytest.mark.parametrize(
    "instances_enabled, repositories_enabled",
    (
        (True, True),
        (True, False),
        (False, True),
    ),
    ids=(
        "instances-repositories-enabled",
        "instances-enabled-repositories-disabled",
        "instances-disabled",
    ),
)
@pytest.mark.parametrize("creation_fails", (False, True), ids=("creation-works", "creation-fails"))
def test_sync_tickets(
    instances_enabled, repositories_enabled, creation_fails, tmp_path, runner, caplog
):
    config = gen_test_config(
        instances_enabled=instances_enabled, repositories_enabled=repositories_enabled
    )
    jira_config = config["general"]["jira"]
    statuses_map = jira_config["statuses"]
    pagure_usermap = config["instances"]["pagure.io"]["usermap"]
    github_usermap = config["instances"]["github.com"]["usermap"]

    config_file = tmp_path / "config.test.toml"
    with config_file.open("w") as fp:
        tomlkit.dump(config, fp)

    partly_wrapped_repos = []

    def create_partly_wrapped_repo(instance, **kwargs):
        repo = repositories.Instance._types_subclasses[instance.type].repo_cls(**kwargs)
        mock.patch.object(repo, "get_open_issues", wraps=repo.get_open_issues).start()
        partly_wrapped_repos.append(repo)
        return repo

    with (
        mock.patch("jira_sync.sync_mgr.JIRA") as JIRA,
        mock.patch("jira_sync.sync_mgr.Instance", wraps=sync_mgr.Instance) as MockInstance,
        mock.patch("jira_sync.main.SyncManager") as MockSyncManager,
        mock.patch("requests.get", wraps=mock_requests_get),
        mock.patch.object(main.log, "setLevel"),
        caplog.at_level("DEBUG"),
    ):
        mock_sync_mgr = None

        def wrap_sync_mgr(*args, **kwargs):
            nonlocal mock_sync_mgr
            real_sync_mgr = sync_mgr.SyncManager(*args, **kwargs)
            mock_sync_mgr = mock.Mock(wraps=real_sync_mgr)
            return mock_sync_mgr

        MockSyncManager.side_effect = wrap_sync_mgr

        JIRA.return_value = jira = mock.Mock()
        jira.get_issues_by_labels.side_effect = mock.Mock(wraps=mock_jira__get_issues_by_labels)
        if creation_fails:
            jira.create_issue.return_value = None
        else:
            jira.create_issue.side_effect = mock.Mock(wraps=partial(mock_jira__create_issue, {}))

        MockInstance.repo_cls = create_partly_wrapped_repo

        result = runner.invoke(
            main.cli, ["sync-tickets", "--config", str(config_file)], catch_exceptions=False
        )

    assert result.exit_code == 0

    JIRA.assert_called_once_with(
        JiraConfig.model_validate(jira_config), run_mode=JiraRunMode.READ_WRITE
    )

    if not (instances_enabled and repositories_enabled):
        # Nothing should have happened.
        for repo in partly_wrapped_repos:
            repo.get_open_issues.assert_not_called()
        assert all(
            "Querying instance" not in m and "Querying repository" not in m for m in caplog.messages
        )
        return

    for repo in partly_wrapped_repos:
        repo.get_open_issues.assert_called_once_with()

    assert jira.get_issues_by_labels.call_args_list == [
        mock.call("label"),
        mock.call("label", closed=True),
    ]
    assert all(f"Querying repository pagure.io:{name}" in caplog.text for name in TEST_PAGURE_REPOS)
    assert all(
        f"Querying repository github.com:{name}" in caplog.text for name in TEST_GITHUB_REPOS
    )

    # One JIRA issue per instance was marked as blocked
    jira_issue = JiraIssue.model_validate(TEST_PAGURE_JIRA_ISSUES[4])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["blocked"])
    assert "CPE-5: Matched with forge issue https://pagure.io/test2/issue/1" in caplog.text
    assert "CPE-5: Transitioning issue from IN_PROGRESS to BLOCKED" in caplog.text
    jira_issue = JiraIssue.model_validate(TEST_GITHUB_JIRA_ISSUES[4])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["blocked"])
    assert "CPE-105: Matched with forge issue https://github.com/test2/issues/1" in caplog.text
    assert "CPE-105: Transitioning issue from IN_PROGRESS to BLOCKED" in caplog.text

    # One JIRA issue per instance was closed upstream
    jira_issue = JiraIssue.model_validate(TEST_PAGURE_JIRA_ISSUES[1])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["closed"])
    assert "CPE-2: Matched with forge issue https://pagure.io/test2/issue/2" in caplog.text
    assert "CPE-2: Transitioning issue from IN_PROGRESS to DONE" in caplog.text
    jira_issue = JiraIssue.model_validate(TEST_GITHUB_JIRA_ISSUES[1])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["closed"])
    assert "CPE-102: Matched with forge issue https://github.com/test2/issues/2" in caplog.text
    assert "CPE-102: Transitioning issue from IN_PROGRESS to DONE" in caplog.text

    # One JIRA issue has to be created per instance, it has no assignee
    new_jira_ids = [len(TEST_JIRA_ISSUES) + 1, len(TEST_JIRA_ISSUES) + 2]
    pagure_issue = TEST_PAGURE_ISSUES[2]
    jira.create_issue.assert_any_call(
        summary=pagure_issue["title"],
        description=pagure_issue["content"],
        url=pagure_issue["full_url"],
        labels=[jira_config["label"], f"pagure.io:{pagure_issue['repo']}"],
        story_points=0,
    )
    assert "Creating JIRA ticket from https://pagure.io/namespace/test1/issue/3" in caplog.text
    github_issue = TEST_GITHUB_ISSUES[2]
    jira.create_issue.assert_any_call(
        summary=github_issue["title"],
        description=github_issue["body"],
        url=github_issue["html_url"],
        labels=[jira_config["label"], f"github.com:{github_issue['repo']}"],
        story_points=0,
    )
    assert "Creating JIRA ticket from https://github.com/org/test1/issues/3" in caplog.text
    if not creation_fails:
        for new_jira_id in new_jira_ids:
            assert f"CPE-{new_jira_id}: Not transitioning issue with status NEW" in caplog.text
    else:
        assert f"Couldn’t create new JIRA issue from '{pagure_issue['full_url']}'" in caplog.text
        assert f"Couldn’t create new JIRA issue from '{github_issue['html_url']}'" in caplog.text
        for new_jira_id in new_jira_ids:
            assert f"CPE-{new_jira_id}: Not transitioning issue with status NEW" not in caplog.text

    # One issue per instance has been assigned meanwhile, update JIRA issues
    pagure_issue = TEST_PAGURE_ISSUES[3]
    jira_issue = JiraIssue.model_validate(TEST_PAGURE_JIRA_ISSUES[0])
    jira.assign_to_issue.assert_any_call(
        jira_issue, pagure_usermap[pagure_issue["assignee"]["name"]]
    )
    assert (
        "CPE-1: Matched with forge issue https://pagure.io/namespace/test1/issue/4" in caplog.text
    )
    assert "CPE-1: Transitioning issue from NEW to IN_PROGRESS" in caplog.text
    github_issue = TEST_GITHUB_ISSUES[3]
    jira_issue = JiraIssue.model_validate(TEST_GITHUB_JIRA_ISSUES[0])
    jira.assign_to_issue.assert_any_call(
        jira_issue, github_usermap[github_issue["assignee"]["login"]]
    )
    assert "CPE-101: Matched with forge issue https://github.com/org/test1/issues/4" in caplog.text
    assert "CPE-101: Transitioning issue from NEW to IN_PROGRESS" in caplog.text

    # One JIRA issue was marked DONE per instance, but it’s been reopened upstream
    jira_issue = JiraIssue.model_validate(TEST_PAGURE_JIRA_ISSUES[3])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["assigned"])
    assert "CPE-4: Matched with forge issue https://pagure.io/test2/issue/6" in caplog.text
    assert "CPE-4: Transitioning issue from DONE to IN_PROGRESS" in caplog.text
    jira_issue = JiraIssue.model_validate(TEST_GITHUB_JIRA_ISSUES[3])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["assigned"])
    assert "CPE-104: Matched with forge issue https://github.com/test2/issues/6" in caplog.text
    assert "CPE-104: Transitioning issue from DONE to IN_PROGRESS" in caplog.text

    # One issue per instance shouldn’t be considered
    assert "https://pagure.io/test2/issue/5" not in caplog.text
    assert "https://github.com/test2/issues/5" not in caplog.text


def test_sync_tickets_authentication_fails(tmp_path, runner):
    config = gen_test_config(instances_enabled=True, repositories_enabled=True)
    config_file = tmp_path / "config.test.toml"
    with config_file.open("w") as fp:
        tomlkit.dump(config, fp)

    with mock.patch.object(main, "SyncManager") as MockSyncManager:
        MockSyncManager.side_effect = JIRAError("BOO")

        result = runner.invoke(
            main.cli,
            ["sync-tickets", "--config", str(config_file)],
        )

    assert "Error: BOO" in result.output
