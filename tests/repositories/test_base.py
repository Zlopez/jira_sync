from itertools import chain
from unittest import mock

import pytest
import requests
from pydantic import AnyUrl

from jira_sync.repositories import base

TEST_API_RESULT = {"foo": {"bar": "baz"}}


class BaseTestRepository:
    cls: type

    @classmethod
    def create_repo(
        cls,
        instance_api_url="https://api.example.net",
        instance_url="https://example.net",
        repo="foo",
        enabled=True,
        token=None,
        label=None,
        blocked_label="blocked",
        usermap=None,
    ):
        usermap = usermap or {}
        return cls.cls(
            instance_api_url=instance_api_url,
            instance_url=instance_url,
            repo=repo,
            enabled=enabled,
            token=token,
            label=label,
            blocked_label=blocked_label,
            usermap=usermap,
        )


class TestRepository(BaseTestRepository):
    cls = base.Repository

    @pytest.mark.parametrize("url_type", (str, AnyUrl))
    @pytest.mark.parametrize("with_api_url", (True, False), ids=("with-api-url", "without-api-url"))
    def test___init__(self, url_type, with_api_url):
        api_url = None
        if issubclass(url_type, str):
            if with_api_url:
                api_url = "https://api.example.net"
            url = "https://example.net"
        else:
            if with_api_url:
                api_url = AnyUrl.build(scheme="https", host="api.example.net")
            url = AnyUrl.build(scheme="https", host="example.net")

        repo = self.create_repo(
            instance_api_url=api_url,
            instance_url=url,
            token="TOKEN",  # noqa: S106
            label="LABEL",
            blocked_label="BLOCKED",
            usermap={"foobar": "snafu"},
        )

        if with_api_url:
            assert repo.instance_api_url == "https://api.example.net"
        else:
            assert repo.instance_api_url is None
        assert repo.instance_url == "https://example.net"
        assert repo.repo == "foo"
        assert repo.enabled
        assert repo.token == "TOKEN"  # noqa: S105
        assert repo.label == "LABEL"
        assert repo.blocked_label == "BLOCKED"
        assert repo.usermap["foobar"] == "snafu"

    def test_from_config(self):
        with mock.patch.dict(base.Repository._types_subclasses):

            class FakeRepository(base.Repository):
                type = "fake"

            repo = base.Repository.from_config(
                {
                    "type": "fake",
                    "instance_api_url": "https://api.test.net",
                    "instance_url": "https://test.net",
                    "repo": "repo",
                    "enabled": True,
                    "token": "TOKEN",
                    "label": "LABEL",
                    "blocked_label": "BLOCKED",
                    "usermap": {},
                }
            )

            assert isinstance(repo, FakeRepository)
            assert repo.instance_api_url == "https://api.test.net"
            assert repo.instance_url == "https://test.net"
            assert repo.repo == "repo"
            assert repo.enabled
            assert repo.token == "TOKEN"  # noqa: S105
            assert repo.label == "LABEL"
            assert repo.blocked_label == "BLOCKED"
            assert repo.usermap == {}

    @pytest.mark.parametrize(
        "selector, result",
        (
            ("", TEST_API_RESULT),
            (None, TEST_API_RESULT),
            ("foo", TEST_API_RESULT["foo"]),
            ("foo.bar", TEST_API_RESULT["foo"]["bar"]),
        ),
        ids=("empty-string", None, "foo", "foo.bar"),
    )
    def test_select_from_result(self, selector, result):
        assert base.Repository.select_from_result(TEST_API_RESULT, selector) == result

    @pytest.mark.parametrize(
        "needs_selector", (True, False), ids=("needs-selector", "doesnt-need-selector")
    )
    def test_get_open_issues(self, needs_selector):
        repo = self.create_repo()

        API_RESULT_PAGES = [[1, 2, 3], [4, 5, 6]]

        if needs_selector:
            repo._api_result_selectors = {"issues": "issues"}
            API_RESULT_PAGES = [{"issues": page} for page in API_RESULT_PAGES]

        # [...status_code=...timeout)] + ... => simulate intermittent error on first result page
        API_RESPONSES = [mock.Mock(status_code=requests.codes.timeout)] + [
            mock.Mock(
                status_code=requests.codes.ok,
                json=mock.Mock(return_value=api_result_page),
            )
            for api_result_page in API_RESULT_PAGES
        ]

        with (
            mock.patch.object(repo, "get_issue_params") as get_issue_params,
            mock.patch.object(repo, "get_next_page") as get_next_page,
            mock.patch.object(repo, "normalize_issue") as normalize_issue,
            mock.patch.object(requests, "get") as requests_get,
        ):
            get_issue_params.return_value = {}
            get_next_page_retvals = [
                {"url": f"https://api.example.net?page={i + 1}"}
                for i in range(len(API_RESULT_PAGES))
            ]
            get_next_page.side_effect = get_next_page_retvals + [None]
            requests_get.side_effect = API_RESPONSES
            normalize_issue.side_effect = lambda x: x

            issues = repo.get_open_issues()

        if needs_selector:
            assert issues == list(chain.from_iterable(res["issues"] for res in API_RESULT_PAGES))
        else:
            assert issues == list(chain.from_iterable(API_RESULT_PAGES))
        get_issue_params.assert_called_once_with()
        assert get_next_page.call_args_list[0] == mock.call(endpoint="issues")
        for response in API_RESPONSES[1:]:  # First request failed…
            get_next_page.assert_any_call(endpoint="issues", response=response)
        assert requests_get.call_args_list == [mock.call(**get_next_page_retvals[0])] + [
            mock.call(**kwargs) for kwargs in get_next_page_retvals
        ]
