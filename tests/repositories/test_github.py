from unittest import mock

import pytest
import requests

from jira_sync.repositories import github
from jira_sync.repositories.base import Issue, IssueStatus

from .test_base import BaseTestInstance, BaseTestRepository


class GitHubTestBase:
    @pytest.mark.parametrize(
        "testcase",
        (
            "first-page",
            "first-page-without-token",
            "first-page-with-headers",
            "next-page",
            "next-page-without-token",
            "next-page-with-headers",
            "next-page-missing-link",
            "last-page",
        ),
    )
    @pytest.mark.parametrize(
        "endpoint", ("an_endpoint", None), ids=("with-endpoint", "without-endpoint")
    )
    def test_get_next_page(self, testcase, endpoint):
        if "first-page" in testcase:
            page = "first-page"
        elif "next-page" in testcase:
            page = "next-page"
        else:
            page = "last-page"

        without_token = "without-token" in testcase
        with_headers = "with-headers" in testcase

        obj = self.create_obj()
        if not without_token:
            obj.token = "TOKEN"  # noqa: S105

        match page:
            case "first-page":
                response = None
            case "next-page":
                if "missing-link" in testcase:
                    headers = {}
                else:
                    headers = {
                        "link": '<https://the.first/page>; rel="first",'
                        + ' <https://the.next/page>; rel="next"'
                    }

                response = mock.Mock(status_code=requests.codes.ok, headers=headers)
            case "last-page":
                headers = {"link": '<https://the.first/page>; rel="first"'}
                response = mock.Mock(status_code=requests.codes.ok, headers=headers)

        if with_headers:
            headers = {"the-header": "the-value"}
        else:
            headers = None

        args = obj.get_next_page(endpoint=endpoint, response=response, headers=headers)

        match page:
            case "first-page":
                expected_base = "https://api.example.net"
                optional_repo = (
                    "/repos/foo" if issubclass(self.cls, github.GitHubRepository) else ""
                )
                optional_endpoint = "/an_endpoint" if endpoint else ""

                assert args["url"] == f"{expected_base}{optional_repo}{optional_endpoint}"
            case "next-page":
                if "missing-link" not in testcase:
                    assert args["url"] == "https://the.next/page"
                else:
                    assert args is None
            case "last-page":
                assert args is None

        if args:
            if without_token:
                assert "Authorization" not in args["headers"]
            else:
                assert args["headers"]["Authorization"] == "Bearer TOKEN"

            if with_headers:
                assert args["headers"]["the-header"] == "the-value"
            else:
                assert "the-header" not in args["headers"]


class TestGitHubInstance(GitHubTestBase, BaseTestInstance):
    cls = github.GitHubInstance


class TestGitHubRepository(GitHubTestBase, BaseTestRepository):
    cls = github.GitHubRepository

    @pytest.mark.parametrize("status", ("closed", "blocked", "new", "assigned"))
    @pytest.mark.parametrize("label_type", (dict, str), ids=("labels-as-dict", "labels-as-str"))
    def test_normalize_issue(self, status, label_type):
        labels = ["one tag", "another tag"]
        if status == "blocked":
            labels.append("blocked")

        if label_type is dict:
            labels = [{"name": label} for label in labels]

        api_result = {
            "html_url": "FULL URL",
            "title": "TITLE",
            "body": "CONTENT",
            "assignee": {"login": "GITHUB_ASSIGNEE"} if status != "new" else None,
            "state": "closed" if status == "closed" else "open",
            "labels": labels,
        }

        repo = self.create_obj()

        issue = repo.normalize_issue(api_result)

        assert isinstance(issue, Issue)

        assert issue.repository is repo
        assert issue.full_url == "FULL URL"
        assert issue.title == "TITLE"
        assert issue.content == "CONTENT"
        if status != "new":
            assert issue.assignee == "GITHUB_ASSIGNEE"
        else:
            assert issue.assignee is None
        assert issue.status == IssueStatus[status]

    @pytest.mark.parametrize("with_label", (True, False), ids=("with-label", "without-label"))
    def test_get_issue_params(self, with_label):
        repo = self.create_obj(label="the-label" if with_label else None)

        params = repo.get_issue_params()

        if with_label:
            assert params["params"]["labels"] == "the-label"
        else:
            assert params == {}
