from unittest import mock

import pytest
import requests

from jira_sync.repositories import pagure
from jira_sync.repositories.base import Issue, IssueStatus

from .test_base import BaseTestInstance, BaseTestRepository


class PagureTestBase:
    @pytest.mark.parametrize("page", ("first-page", "next-page", "last-page"))
    @pytest.mark.parametrize("with_params", (True, False), ids=("with-params", "without-params"))
    @pytest.mark.parametrize(
        "endpoint", ("an_endpoint", None), ids=("with-endpoint", "without-endpoint")
    )
    def test_get_next_page(self, page, with_params, endpoint):
        obj = self.create_obj()

        match page:
            case "first-page":
                response = None
            case "next-page":
                response = mock.Mock(
                    status_code=requests.codes.ok,
                    json=mock.Mock(return_value={"pagination": {"next": "the next page url"}}),
                )
            case "last-page":
                response = mock.Mock(
                    status_code=requests.codes.ok,
                    json=mock.Mock(return_value={"pagination": {"next": None}}),
                )

        if with_params:
            kwargs = {"params": {"the_passed_params": "the params"}}
        else:
            kwargs = {}

        args = obj.get_next_page(endpoint=endpoint, response=response, **kwargs)

        match page:
            case "first-page":
                expected_base = "https://example.net/api/0"
                optional_repo = "/foo" if issubclass(self.cls, pagure.PagureRepository) else ""
                optional_endpoint = "/an_endpoint" if endpoint else ""

                assert args["url"] == f"{expected_base}{optional_repo}{optional_endpoint}"
            case "next-page":
                assert args["url"] == "the next page url"
            case "last-page":
                assert args is None

        if page != "last-page":
            if with_params:
                assert args["params"] == kwargs["params"]
            else:
                assert "params" not in args


class TestPagureInstance(PagureTestBase, BaseTestInstance):
    cls = pagure.PagureInstance

    @classmethod
    def create_obj(cls, **kwargs):
        kwargs.setdefault("instance_url", "https://example.net")
        kwargs.setdefault("instance_api_url", None)
        return super().create_obj(**kwargs)

    @pytest.mark.parametrize("with_api_url", (False, True), ids=("without-api-url", "with-api-url"))
    def test___init__(self, with_api_url: bool):
        obj = self.create_obj(
            instance_url="INSTANCE_URL",
            instance_api_url="INSTANCE_API_URL" if with_api_url else None,
        )

        if with_api_url:
            assert obj.instance_api_url == "INSTANCE_API_URL"
        else:
            assert obj.instance_api_url == "INSTANCE_URL/api/0"


class TestPagureRepository(PagureTestBase, BaseTestRepository):
    cls = pagure.PagureRepository

    default_instance = TestPagureInstance.create_obj()

    @pytest.mark.parametrize("status", ("closed", "blocked", "new", "assigned"))
    def test_normalize_issue(self, status):
        api_result = {
            "full_url": "FULL URL",
            "title": "TITLE",
            "content": "CONTENT",
            "assignee": {"name": "PAGURE_ASSIGNEE"} if status != "new" else None,
            "status": status,
            "tags": ["one tag", "another tag"],
        }

        if status == "blocked":
            api_result["tags"].append("blocked")

        repo = self.create_obj()

        issue = repo.normalize_issue(api_result)

        assert isinstance(issue, Issue)

        assert issue.repository is repo
        assert issue.full_url == "FULL URL"
        assert issue.title == "TITLE"
        assert issue.content == "CONTENT"
        if status != "new":
            assert issue.assignee == "PAGURE_ASSIGNEE"
        else:
            assert issue.assignee is None
        assert issue.status == IssueStatus[status]

    @pytest.mark.parametrize("with_label", (True, False), ids=("with-label", "without-label"))
    def test_get_issue_params(self, with_label):
        repo = self.create_obj(label="the-label" if with_label else None)

        params = repo.get_issue_params()

        if with_label:
            assert params["params"]["tags"] == "the-label"
        else:
            assert params == {}
