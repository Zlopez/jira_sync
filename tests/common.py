from collections.abc import Collection
from unittest import mock
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import requests
from pydantic import BaseModel


class HashableModel(BaseModel):
    def __hash__(self) -> int:
        return hash((type(self),) + tuple(self.__dict__.items()))


class JiraStatus(HashableModel):
    name: str = "NEW"


class JiraAssignee(HashableModel):
    key: str = "other_jira_user"
    emailAddress: str = "other-address@example.com"


class JiraIssueFields(HashableModel):
    description: str | None = None
    assignee: JiraAssignee | None = None
    status: JiraStatus = JiraStatus()
    labels: tuple[str, ...] = ()


class JiraIssue(HashableModel):
    summary: str | None = None
    key: str | None = None
    fields: JiraIssueFields = JiraIssueFields()


# Pagure
TEST_PAGURE_ISSUES = [
    {
        "id": id_,
        "title": f"A thing happened on Pagure! -- {id_}",
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
TEST_PAGURE_REPOS: dict[str, dict[str, str]] = {
    "namespace/test1": {"label": ""},
    "test2": {"label": "test"},
}
TEST_PAGURE_JIRA_ISSUES = [
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
                "fields": {
                    "labels": ["foo", "label", "pagure.io:namespace/test1"],
                    "description": "https://pagure.io/namespace/test1/issue/4",
                    "status": {"name": "NEW"},
                },
            },
            {
                "fields": {
                    "labels": ["bar", "label", "pagure.io:test2"],
                    "description": "https://pagure.io/test2/issue/2",
                    "status": {"name": "IN_PROGRESS"},
                },
            },
            {"fields": {"labels": ["hello"]}},
            {
                "fields": {
                    "labels": ["label", "pagure.io:test2"],
                    "description": "https://pagure.io/test2/issue/6",
                    "status": {"name": "DONE"},
                },
            },
            {
                "fields": {
                    "labels": ["label", "pagure.io:test2"],
                    "description": "https://pagure.io/test2/issue/1",
                    "status": {"name": "IN_PROGRESS"},
                },
            },
            {
                "fields": {
                    "labels": ["label", "pagure.io:namespace/test1"],
                    "description": None,
                    "status": {"name": "CONFUSED"},
                },
            },
            # This one should be last
            {
                "fields": {
                    "labels": ["label", "pagure.io:namespace/test1"],
                    "description": "https://pagure.io/namespace/test1/issue/7",
                    "status": {"name": "MISSED_THE_BUS"},
                },
            },
        ),
        start=1,
    )
]

# GitHub
TEST_GITHUB_ISSUES = [
    {
        "number": number,
        "title": f"A thing happened on GitHub! -- {number}",
        "body": f"Fix it! -- {number}",
        "assignee": None,
        "labels": (),
        "html_url": f"https://github.com/{spec['repo']}/issues/{number}",
        "state": "open",
    }
    | spec
    for number, spec in enumerate(
        (
            {
                "repo": "test2",
                "body": "The thing tripped over the other thing.",
                "labels": ({"name": "test"}, {"name": "blocked"}),
            },
            {
                "repo": "test2",
                "labels": ({"name": "easyfix"}, {"name": "test"}),
                "assignee": {"login": "hotdog"},
                "state": "closed",
            },
            {"repo": "org/test1", "labels": ({"name": "label"},)},
            {"repo": "org/test1", "assignee": {"login": "zod"}},
            {
                "repo": "space_the_final_frontier",
                "labels": ({"name": "catch_me_if_you_can"},),
            },
            {
                "repo": "test2",
                "labels": ({"name": "test"},),
                "assignee": {"login": "you_yeah_you"},
            },
            {"repo": "org/test1"},
        ),
        start=1,
    )
]
TEST_GITHUB_TO_JIRA_USERS = {
    issue["assignee"]["login"]: "jira_" + issue["assignee"]["login"]
    for issue in TEST_GITHUB_ISSUES
    if issue["assignee"] and "login" in issue["assignee"]
}
TEST_GITHUB_REPOS: dict[str, dict[str, str]] = {
    "org/test1": {"label": ""},
    "test2": {"label": "test"},
}
TEST_GITHUB_JIRA_ISSUES = [
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
                "fields": {
                    "labels": ["foo", "label", "github.com:org/test1"],
                    "description": "https://github.com/org/test1/issues/4",
                    "status": {"name": "NEW"},
                },
            },
            {
                "fields": {
                    "labels": ["bar", "label", "github.com:test2"],
                    "description": "https://github.com/test2/issues/2",
                    "status": {"name": "IN_PROGRESS"},
                },
            },
            {"fields": {"labels": ["hello"]}},
            {
                "fields": {
                    "labels": ["label", "github.com:test2"],
                    "description": "https://github.com/test2/issues/6",
                    "status": {"name": "DONE"},
                },
            },
            {
                "fields": {
                    "labels": ["label", "github.com:test2"],
                    "description": "https://github.com/test2/issues/1",
                    "status": {"name": "IN_PROGRESS"},
                },
            },
            {
                "fields": {
                    "labels": ["label", "github.com:org/test1"],
                    "description": None,
                    "status": {"name": "CONFUSED"},
                },
            },
            # This one should be last
            {
                "fields": {
                    "labels": ["label", "github.com:org/test1"],
                    "description": "https://github.com/org/test1/issues/7",
                    "status": {"name": "MISSED_THE_BUS"},
                },
            },
        ),
        start=101,
    )
]

TEST_JIRA_ISSUES = TEST_PAGURE_JIRA_ISSUES + TEST_GITHUB_JIRA_ISSUES


def mock_jira__get_issues_by_labels(labels: str | Collection[str], closed=False):
    if isinstance(labels, str):
        labels = [labels]
    return [
        issue
        for issue in TEST_JIRA_ISSUES
        if any(label in issue.fields.labels for label in labels)
        and (issue.fields.status.name == "DONE") == closed
    ]


def mock_jira__create_issue(
    context: dict,
    summary: str,
    description: str,
    url: str,
    labels: Collection[str] | str,
):
    if isinstance(labels, str):
        labels = (labels,)

    # Don’t reuse issue ids
    context["id"] = id_ = context.get("id", len(TEST_JIRA_ISSUES)) + 1
    return JiraIssue.model_validate(
        {
            "key": f"CPE-{id_}",
            "summary": summary,
            "labels": labels,
            "fields": {
                "description": f"{url}\n\n{description}",
                "status": {"name": "NEW"},
            },
        }
    )


def mock_requests_get(url, params=None, headers=None):
    params = params or {}
    parsed_url = urlsplit(url)
    base_url = urlunsplit((parsed_url.scheme, parsed_url.netloc, parsed_url.path, None, None))
    parsed_query = dict(parse_qsl(parsed_url.query))
    params = parsed_query | params

    # Always paginate only one item per page, ignore per_page settings
    pagination = {"per_page": 1, "page": 1}
    if "page" in params:
        pagination["page"] = int(params["page"])

    # Pagure
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

    # GitHub
    if (
        "api.github" in parsed_url.netloc
        and parsed_url.path.startswith("/repos/")
        and parsed_url.path.rstrip("/").endswith("/issues")
    ):
        html_base_url = base_url.replace("api.", "").replace("/repos/", "/")
        results = [
            issue for issue in TEST_GITHUB_ISSUES if issue["html_url"].startswith(html_base_url)
        ]
        results_len = len(results)

        result_item = pagination["per_page"] * pagination["page"] - 1
        if result_item < results_len - 1:
            next_page = f"{base_url}?per_page=1&page={result_item + 2}"
        else:
            next_page = None
        if result_item:
            prev_page = f"{base_url}?per_page=1&page={result_item}"
        else:
            prev_page = None

        link_items = {
            "first": f"{base_url}?per_page=1&page=1",
            "last": f"{base_url}?per_page=1&page={results_len}",
            "next": next_page,
            "prev": prev_page,
        }

        if not result_item:
            del link_items["first"]
            del link_items["prev"]

        if result_item == results_len - 1:
            del link_items["last"]
            del link_items["next"]

        new_headers = {
            "link": ", ".join(f'<{url}>; rel="{rel}"' for rel, url in link_items.items())
        }

        try:
            paged_results = [results[result_item]]
        except IndexError:
            paged_results = []

        response = mock.Mock(status_code=requests.codes.ok)
        response.headers = new_headers
        response.json.return_value = paged_results

        return response

    # Catch requests to unhandled websites
    raise RuntimeError


def gen_test_config(*, instances_enabled, repositories_enabled):
    test_pagure_repos = {
        name: spec | {"enabled": repositories_enabled} for name, spec in TEST_PAGURE_REPOS.items()
    }
    test_github_repos = {
        name: spec | {"enabled": repositories_enabled} for name, spec in TEST_GITHUB_REPOS.items()
    }

    return {
        "general": {
            "jira": {
                "instance_url": "https://jira.atlassian.com",
                "project": "Project",
                "token": "token",
                "default_issue_type": "Story",
                "label": "label",
                "story_points_field": "story_points",
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
                "enabled": instances_enabled,
                "instance_url": "https://pagure.io/",
                "blocked_label": "blocked",
                "story_points": {
                    "label1": 1,
                    "label2": 5,
                    "label3": 10,
                },
                "usermap": TEST_PAGURE_TO_JIRA_USERS.copy(),
                "repositories": test_pagure_repos,
            },
            "github.com": {
                "type": "github",
                "enabled": instances_enabled,
                "instance_url": "https://github.com",
                "instance_api_url": "https://api.github.com",
                "blocked_label": "blocked",
                "usermap": TEST_GITHUB_TO_JIRA_USERS.copy(),
                "repositories": test_github_repos,
            },
        },
    }
