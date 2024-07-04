import logging
from pathlib import Path
from unittest import mock

import pytest
import tomlkit
from click.testing import CliRunner
from dotwiz import DotWiz

from jira_sync import main

ROOTDIR = Path(__file__).parent.parent
CONFIG_EXAMPLE_FILE = ROOTDIR / "config.example.toml"


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

    with mock.patch.object(main, "log") as log:
        result = runner.invoke(main.cli, cmd_args, catch_exceptions=False)

    assert result.exit_code == 0

    if verbose:
        log.setLevel.assert_called_once_with(logging.DEBUG)
    else:
        log.setLevel.assert_called_once_with(logging.INFO)


TEST_PAGURE_ISSUES = [
    {
        "id": id_,
        "title": f"A thing happened! -- {id_}",
        "content": f"Fix it! -- {id_}",
        "assignee": None,
        "tags": (),
        "full_url": f"https://pagure.io/{spec['repo']}/issue/{id_}",
        "status": "Open",
    }
    | spec
    for id_, spec in enumerate(
        (
            {
                "repo": "test2",
                "content": "The thing tripped over the other thing.",
                "tags": (
                    "test",
                    "blocked",
                ),
            },
            {
                "repo": "test2",
                "tags": ("easyfix", "test"),
                "assignee": {"name": "hotdog"},
                "status": "Closed",
            },
            {"repo": "namespace/test1", "tags": ("label",)},
            {"repo": "namespace/test1", "assignee": {"name": "zod"}},
            {
                "repo": "space_the_final_frontier",
                "tags": ("catch_me_if_you_can"),
            },
            {
                "repo": "test2",
                "tags": ("test",),
                "assignee": {"name": "you_yeah_you"},
            },
            {"repo": "namespace/test1"},
        ),
        start=1,
    )
]
TEST_PAGURE_TO_JIRA_USERS = {
    issue["assignee"]["name"]: "jira_" + issue["assignee"]["name"]
    for issue in TEST_PAGURE_ISSUES
    if issue["assignee"] and "name" in issue["assignee"]
}
TEST_JIRA_ISSUES = [
    DotWiz(**{"key": f"CPE-{id_}", "fields": {"description": "BOO"}} | spec)
    for id_, spec in enumerate(
        (
            {
                "labels": ["foo", "namespace/test1"],
                "fields": {
                    "description": "https://pagure.io/namespace/test1/issue/4",
                    "status": {"name": "NEW"},
                },
            },
            {
                "labels": ["bar", "test2"],
                "fields": {
                    "description": "https://pagure.io/test2/issue/2",
                    "status": {"name": "IN_PROGRESS"},
                },
            },
            {"labels": ["hello"]},
            {
                "labels": ["test2"],
                "fields": {
                    "description": "https://pagure.io/test2/issue/6",
                    "status": {"name": "DONE"},
                },
            },
            {
                "labels": ["test2"],
                "fields": {
                    "description": "https://pagure.io/test2/issue/1",
                    "status": {"name": "IN_PROGRESS"},
                },
            },
            {
                "labels": ["namespace/test1"],
                "fields": {
                    "description": "https://pagure.io/namespace/test1/issue/7",
                    "status": {"name": "MISSED_THE_BUS"},
                },
            },
        ),
        start=1,
    )
]
TEST_REPOS: dict[str, dict[str, str]] = {
    "namespace/test1": {"label": ""},
    "test2": {"label": "test"},
}


def _jira__get_open_issues_by_label(label: str):
    return [issue for issue in TEST_JIRA_ISSUES if label in issue.labels]


def _jira__get_issue_by_link(url: str, repo: str, title: str):
    candidates = [
        issue
        for issue in TEST_JIRA_ISSUES
        if url in issue.fields.description and repo in issue.labels
    ]
    if not candidates:
        return
    for candidate in candidates:
        if candidate.fields.description.startswith(f"{url}\n"):
            return candidate
    return candidates[0]


def _jira__create_issue(summary: str, description: str, url: str, label: str, _meta={}):
    # Don’t reuse issue ids
    _meta["id"] = id_ = _meta.get("id", len(TEST_JIRA_ISSUES)) + 1
    return DotWiz(
        {
            "key": f"CPE-{id_}",
            "summary": summary,
            "labels": [label] if label else [],
            "fields": {
                "description": f"{url}\n\n{description}",
                "status": {"name": "NEW"},
            },
        }
    )


def _pagure__get_open_project_issues(repo: str, label: str):
    return [
        issue
        for issue in TEST_PAGURE_ISSUES
        if issue["repo"] == repo and (not label or label in issue["tags"])
    ]


@pytest.mark.parametrize("pagure_enabled", (True, False), ids=("pagure-enabled", "pagure-disabled"))
def test_sync_tickets(pagure_enabled, tmp_path, runner, caplog):
    with CONFIG_EXAMPLE_FILE.open("r") as fp:
        config = tomlkit.load(fp)

    if not pagure_enabled:
        config["Pagure"]["enabled"] = False

    config_file = tmp_path / "config.test.toml"
    general_config = config["General"]
    states_map = general_config["states"]
    pagure_config = config["Pagure"]
    pagure_usernames = pagure_config["usernames"] = TEST_PAGURE_TO_JIRA_USERS

    general_config["jira_project"] = "CPE"
    pagure_config["repositories"] = [
        {"repo": name, "label": repo["label"]} for name, repo in TEST_REPOS.items()
    ]

    with config_file.open("w") as fp:
        tomlkit.dump(config, fp)

    with (
        mock.patch("jira_sync.main.JIRA") as JIRA,
        mock.patch("jira_sync.main.Pagure") as Pagure,
        mock.patch.object(main.log, "setLevel"),
        caplog.at_level("DEBUG"),
    ):
        JIRA.return_value = jira = mock.Mock()
        jira.get_open_issues_by_label.side_effect = mock.Mock(wraps=_jira__get_open_issues_by_label)
        jira.get_issue_by_link.side_effect = mock.Mock(wraps=_jira__get_issue_by_link)
        jira.create_issue.side_effect = mock.Mock(wraps=_jira__create_issue)

        Pagure.return_value = pagure = mock.Mock()
        pagure.get_open_project_issues.side_effect = mock.Mock(
            wraps=_pagure__get_open_project_issues
        )

        result = runner.invoke(
            main.cli, ["sync-tickets", "--config", str(config_file)], catch_exceptions=False
        )

    assert result.exit_code == 0

    JIRA.assert_called_once_with(
        url=general_config["jira_instance"],
        token=general_config["jira_token"],
        project=general_config["jira_project"],
        issue_type=general_config["jira_default_issue_type"],
    )

    if not pagure_enabled:
        # Nothing should have happened.
        Pagure.assert_not_called()
        assert not caplog.text
        return

    Pagure.assert_called_once_with(pagure_config["pagure_url"])

    assert all(
        mock.call(name) in jira.get_open_issues_by_label.call_args_list for name in TEST_REPOS
    )
    assert all(
        mock.call(name, repo["label"]) in pagure.get_open_project_issues.call_args_list
        for name, repo in TEST_REPOS.items()
    )
    assert all(f"Processing repository: {name}" in caplog.text for name in TEST_REPOS)

    # One JIRA issue was marked as blocked
    jira_issue = TEST_JIRA_ISSUES[4]
    jira.transition_issue.assert_any_call(jira_issue, states_map["blocked"])
    assert "Processing issue: https://pagure.io/test2/issue/1" in caplog.text
    assert "Issue https://pagure.io/test2/issue/1 matched with CPE-5" in caplog.text
    assert "Transition issue CPE-5 from IN_PROGRESS to BLOCKED" in caplog.text

    # One JIRA issue was closed upstream in Pagure
    jira_issue = TEST_JIRA_ISSUES[1]
    jira.transition_issue.assert_any_call(jira_issue, states_map["closed"])
    assert "Processing issue: https://pagure.io/test2/issue/2" in caplog.text
    assert "Issue https://pagure.io/test2/issue/2 matched with CPE-2" in caplog.text
    assert "Marking issue CPE-2 for closing" in caplog.text
    assert "Closing 1 JIRA issues: CPE-2" in caplog.text

    # One JIRA issue has to be created, it has no assignee
    pagure_issue = TEST_PAGURE_ISSUES[2]
    new_jira_id = len(TEST_JIRA_ISSUES) + 1
    jira.create_issue.assert_any_call(
        pagure_issue["title"],
        pagure_issue["content"],
        pagure_issue["full_url"],
        pagure_issue["repo"],
    )
    assert "Processing issue: https://pagure.io/namespace/test1/issue/3" in caplog.text
    assert "Creating jira ticket from 'https://pagure.io/namespace/test1/issue/3'" in caplog.text
    assert f"Not transitioning issue CPE-{new_jira_id} with state NEW" in caplog.text

    # One Pagure issue has been assigned meanwhile, update JIRA issue
    pagure_issue = TEST_PAGURE_ISSUES[3]
    jira_issue = TEST_JIRA_ISSUES[0]
    jira.assign_to_issue.assert_any_call(
        jira_issue, pagure_usernames[pagure_issue["assignee"]["name"]]
    )
    assert "Processing issue: https://pagure.io/namespace/test1/issue/4" in caplog.text
    assert "Issue https://pagure.io/namespace/test1/issue/4 matched with CPE-1" in caplog.text
    assert "Transition issue CPE-1 from NEW to IN_PROGRESS" in caplog.text

    # One JIRA issue was marked DONE, but it’s been reopened in Pagure
    jira_issue = TEST_JIRA_ISSUES[3]
    jira.transition_issue.assert_any_call(jira_issue, states_map["assigned"])
    assert "Processing issue: https://pagure.io/test2/issue/6" in caplog.text
    assert "Issue https://pagure.io/test2/issue/6 matched with CPE-4" in caplog.text
    assert "Transition issue CPE-4 from DONE to IN_PROGRESS" in caplog.text

    # One Pagure issue shouldn’t be considered
    assert "Processing issue: https://pagure.io/test2/issue/5" not in caplog.text
