import logging
from functools import partial
from unittest import mock
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import pytest
import requests
import tomlkit
from click.testing import CliRunner
from pydantic import BaseModel, HttpUrl

from jira_sync import main


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


class HashableModel(BaseModel):
    def __hash__(self) -> int:
        return hash((type(self),) + tuple(self.__dict__.items()))


class JiraStatus(HashableModel):
    name: str


class JiraIssueFields(HashableModel):
    description: str | None
    status: JiraStatus


class JiraIssue(HashableModel):
    summary: str
    key: str
    fields: JiraIssueFields
    labels: tuple[str, ...] = ()


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
                "tags": ("catch_me_if_you_can",),
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
    JiraIssue.model_validate(
        {
            "key": f"CPE-{id_}",
            "summary": "BOO",
            "fields": {"description": "BOO BOO", "status": {"name": "UNSET"}},
        }
        | spec
    )
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
                "fields": {"description": None, "status": {"name": "CONFUSED"}},
            },
            # This one should be last
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
        if issue.fields.description and url in issue.fields.description and repo in issue.labels
    ]
    if not candidates:
        return
    for candidate in candidates:
        if candidate.fields.description.startswith(f"{url}\n"):
            return candidate
    return candidates[0]


def _jira__create_issue(context: dict, summary: str, description: str, url: str, label: str):
    # Don’t reuse issue ids
    context["id"] = id_ = context.get("id", len(TEST_JIRA_ISSUES)) + 1
    return JiraIssue.model_validate(
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


def mock_requests_get(url, params=None):
    params = params or {}
    parsed_url = urlsplit(url)
    base_url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, None, None))
    parsed_query = dict(parse_qsl(parsed_url.query))
    params = parsed_query | params

    results = None
    pagination = {"per_page": 1, "page": 1} | {
        name: int(value) for name, value in params.items() if name in ("per_page", "page")
    }

    if (
        "pagure" in parsed_url.netloc
        and parsed_url.path.startswith("/api/0/")
        and parsed_url.path.rstrip("/").endswith("/issues")
    ):
        api_base_url = base_url.rstrip("/")[:-7]
        api_issues_url = api_base_url + "/issues"

        repo_base_url = api_base_url.replace("/api/0/", "/")
        issue_base_url = repo_base_url + "/issue/"

        results = [
            issue for issue in TEST_PAGURE_ISSUES if issue["full_url"].startswith(issue_base_url)
        ]
        results_len = len(results)

        # Always paginate only one item per page
        result_item = pagination["per_page"] * pagination["page"] - 1
        if result_item < results_len - 1:
            next_page = f"{api_issues_url}?per_page=1&page={result_item + 2}"
        else:
            next_page = None
        if result_item:
            prev_page = f"{api_issues_url}?per_page=1&page={result_item}"
        else:
            prev_page = None

        new_pagination = {
            "first": f"{api_issues_url}?per_page=1&page=1",
            "last": f"{api_issues_url}?per_page=1&page={results_len}",
            "next": next_page,
            "page": result_item + 1,
            "pages": results_len,
            "prev": prev_page,
        }

        try:
            paged_results = [results[result_item]]
        except IndexError:
            paged_results = []

        result_json = {
            "issues": paged_results,
            "pagination": new_pagination,
        }

        response = mock.Mock(status_code=requests.codes.ok)
        response.json.return_value = result_json

        return response

    # Catch requests to unhandled websites
    raise RuntimeError


def gen_test_config(enabled):
    return {
        "general": {
            "jira": {
                "instance_url": "https://jira.atlassian.com",
                "project": "Project",
                "token": "token",
                "default_issue_type": "Story",
                "label": "label",
                "statuses": {
                    "new": "NEW",
                    "assigned": "IN_PROGRESS",
                    "blocked": "BLOCKED",
                    "closed": "DONE",
                },
            }
        },
        "instances": {
            "pagure.io": {
                "type": "pagure",
                "enabled": enabled,
                "instance_url": "https://pagure.io/",
                "blocked_label": "blocked",
                "usermap": TEST_PAGURE_TO_JIRA_USERS.copy(),
                "repositories": TEST_REPOS.copy(),
            }
        },
    }


@pytest.mark.parametrize("enabled", (True, False), ids=("pagure-enabled", "pagure-disabled"))
@pytest.mark.parametrize("creation_fails", (False, True), ids=("creation-works", "creation-fails"))
def test_sync_tickets(enabled, creation_fails, tmp_path, runner, caplog):
    config = gen_test_config(enabled=enabled)
    jira_config = config["general"]["jira"]
    statuses_map = jira_config["statuses"]
    pagure_usermap = config["instances"]["pagure.io"]["usermap"]

    config_file = tmp_path / "config.test.toml"
    with config_file.open("w") as fp:
        tomlkit.dump(config, fp)

    real_repo_cls = main.Repository
    partly_wrapped_repos = []

    def _repo_from_config(config):
        repo = real_repo_cls.from_config(config=config)
        mock.patch.object(repo, "get_open_issues", wraps=repo.get_open_issues).start()
        partly_wrapped_repos.append(repo)
        return repo

    with (
        mock.patch("jira_sync.main.JIRA") as JIRA,
        mock.patch("jira_sync.main.Repository", wraps=main.Repository) as MockRepo,
        mock.patch("requests.get", wraps=mock_requests_get),
        mock.patch.object(main.log, "setLevel"),
        caplog.at_level("DEBUG"),
    ):
        JIRA.return_value = jira = mock.Mock()
        jira.get_open_issues_by_label.side_effect = mock.Mock(wraps=_jira__get_open_issues_by_label)
        jira.get_issue_by_link.side_effect = mock.Mock(wraps=_jira__get_issue_by_link)
        if creation_fails:
            jira.create_issue.return_value = None
        else:
            jira.create_issue.side_effect = mock.Mock(wraps=partial(_jira__create_issue, {}))

        MockRepo.from_config.side_effect = _repo_from_config

        result = runner.invoke(
            main.cli, ["sync-tickets", "--config", str(config_file)], catch_exceptions=False
        )

    assert result.exit_code == 0

    JIRA.assert_called_once_with(
        url=str(HttpUrl(jira_config["instance_url"])),
        token=jira_config["token"],
        project=jira_config["project"],
        issue_type=jira_config["default_issue_type"],
    )

    if not enabled:
        # Nothing should have happened.
        for repo in partly_wrapped_repos:
            repo.get_open_issues.assert_not_called()
        assert all(m.startswith("Processing repository:") for m in caplog.messages)
        return

    for repo in partly_wrapped_repos:
        repo.get_open_issues.assert_called_once_with()

    assert all(
        mock.call(name) in jira.get_open_issues_by_label.call_args_list for name in TEST_REPOS
    )
    assert all(f"Processing repository: {name}" in caplog.text for name in TEST_REPOS)

    # One JIRA issue was marked as blocked
    jira_issue = JiraIssue.model_validate(TEST_JIRA_ISSUES[4])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["blocked"])
    assert "Processing repo issue: https://pagure.io/test2/issue/1" in caplog.text
    assert "Repo issue https://pagure.io/test2/issue/1 matched with CPE-5" in caplog.text
    assert "Transition issue CPE-5 from IN_PROGRESS to BLOCKED" in caplog.text

    # One JIRA issue was closed upstream in Pagure
    jira_issue = JiraIssue.model_validate(TEST_JIRA_ISSUES[1])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["closed"])
    assert "Processing repo issue: https://pagure.io/test2/issue/2" in caplog.text
    assert "Repo issue https://pagure.io/test2/issue/2 matched with CPE-2" in caplog.text
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
    assert "Processing repo issue: https://pagure.io/namespace/test1/issue/3" in caplog.text
    assert "Creating jira ticket from 'https://pagure.io/namespace/test1/issue/3'" in caplog.text
    if not creation_fails:
        assert f"Not transitioning issue CPE-{new_jira_id} with status NEW" in caplog.text
    else:
        assert f"Couldn’t create new JIRA issue from '{pagure_issue['full_url']}'" in caplog.text
        assert f"Not transitioning issue CPE-{new_jira_id} with status NEW" not in caplog.text

    # One Pagure issue has been assigned meanwhile, update JIRA issue
    pagure_issue = TEST_PAGURE_ISSUES[3]
    jira_issue = JiraIssue.model_validate(TEST_JIRA_ISSUES[0])
    jira.assign_to_issue.assert_any_call(
        jira_issue, pagure_usermap[pagure_issue["assignee"]["name"]]
    )
    assert "Processing repo issue: https://pagure.io/namespace/test1/issue/4" in caplog.text
    assert "Repo issue https://pagure.io/namespace/test1/issue/4 matched with CPE-1" in caplog.text
    assert "Transition issue CPE-1 from NEW to IN_PROGRESS" in caplog.text

    # One JIRA issue was marked DONE, but it’s been reopened in Pagure
    jira_issue = JiraIssue.model_validate(TEST_JIRA_ISSUES[3])
    jira.transition_issue.assert_any_call(jira_issue, statuses_map["assigned"])
    assert "Processing repo issue: https://pagure.io/test2/issue/6" in caplog.text
    assert "Repo issue https://pagure.io/test2/issue/6 matched with CPE-4" in caplog.text
    assert "Transition issue CPE-4 from DONE to IN_PROGRESS" in caplog.text

    # One Pagure issue shouldn’t be considered
    assert "Processing repo issue: https://pagure.io/test2/issue/5" not in caplog.text
