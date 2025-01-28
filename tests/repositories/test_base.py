from contextlib import nullcontext
from itertools import chain
from unittest import mock
from weakref import ProxyType

import pytest
import requests
from pydantic import AnyUrl

from jira_sync.config import model
from jira_sync.repositories import base

TEST_API_RESULT = {"foo": {"bar": "baz"}}


class MockResponse(mock.Mock):
    def raise_for_status(self):
        # Mimick requests.Response.raise_for_status()
        if 400 <= self.status_code < 500:
            raise requests.HTTPError(f"{self.status_code} Client Error: ...")
        if 500 <= self.status_code < 600:
            raise requests.HTTPError(f"{self.status_code} Server Error: ...")


class TestAPIBase:
    def test_sanitize_requests_params(self):
        assert base.APIBase.sanitize_requests_params({"url": "URL", "illegal": "ILLEGAL"}) == {
            "url": "URL"
        }

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
        assert base.APIBase.select_from_result(TEST_API_RESULT, selector) == result


class BaseTestInstance:
    cls: type = base.Instance

    @classmethod
    def create_obj(
        cls,
        name="INSTANCE_NAME",
        instance_url="https://example.net",
        instance_api_url="https://api.example.net",
        repo="foo",
        enabled=True,
        token=None,
        label=None,
        blocked_label="blocked",
        usermap=None,
        labels_to_story_points={},
        query_repositories=(),
        repositories={},
    ):
        usermap = usermap or {}
        return cls.cls(
            name=name,
            instance_url=instance_url,
            instance_api_url=instance_api_url,
            enabled=enabled,
            token=token,
            label=label,
            blocked_label=blocked_label,
            usermap=usermap,
            labels_to_story_points=labels_to_story_points,
            query_repositories=query_repositories,
            repositories=repositories,
        )


class TestInstance(BaseTestInstance):
    cls = base.Instance

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

        instance = self.create_obj(
            instance_url=url,
            instance_api_url=api_url,
            token="TOKEN",  # noqa: S106
            label="LABEL",
            blocked_label="BLOCKED",
            usermap={"foobar": "snafu"},
            repositories={"repo": {}},
        )

        if with_api_url:
            assert instance.instance_api_url == "https://api.example.net"
        else:
            assert instance.instance_api_url is None
        assert instance.instance_url == "https://example.net"
        assert instance.enabled
        assert instance.token == "TOKEN"  # noqa: S105
        assert instance.label == "LABEL"
        assert instance.blocked_label == "BLOCKED"
        assert instance.usermap["foobar"] == "snafu"
        assert isinstance(instance.repositories["repo"], base.Repository)

    def test_from_config(self):
        with mock.patch.dict(base.Instance._types_subclasses):

            class FakeInstance(base.Instance):
                type = "fake"

            instance = base.Instance.from_config(
                name="FAKE_INSTANCE",
                config_path=object(),
                config=model.InstanceConfigBase.model_validate(
                    {
                        "type": "fake",
                        "instance_api_url": "https://api.test.net",
                        "instance_url": "https://test.net",
                        "enabled": True,
                        "token": "TOKEN",
                        "label": "LABEL",
                        "blocked_label": "BLOCKED",
                        "usermap": {},
                        "repositories": {"repo": {}},
                    }
                ),
            )

            assert isinstance(instance, FakeInstance)
            assert instance.name == "FAKE_INSTANCE"
            assert instance.instance_api_url == "https://api.test.net"
            assert instance.instance_url == "https://test.net"
            assert instance.enabled
            assert instance.token == "TOKEN"  # noqa: S105
            assert instance.label == "LABEL"
            assert instance.blocked_label == "BLOCKED"
            assert instance.usermap == {}
            assert isinstance(instance.repositories["repo"], base.Repository)

    @pytest.mark.parametrize("with_api_url", (False, True), ids=("without-api-url", "with-api-url"))
    def test_get_base_url(self, with_api_url: bool):
        instance_url = "URL"
        if with_api_url:
            instance_api_url = "APIURL"
        else:
            instance_api_url = None

        instance = self.create_obj(instance_url=instance_url, instance_api_url=instance_api_url)
        if with_api_url:
            assert instance.get_base_url() == instance_api_url
        else:
            assert instance.get_base_url() == instance_url


class BaseTestRepository:
    cls: type

    default_instance = BaseTestInstance.create_obj()

    @classmethod
    def create_obj(cls, instance=None, name="foo", **config_params):
        if instance is None:
            instance = cls.default_instance
        return cls.cls(instance=instance, name=name, **config_params)


class TestRepository(BaseTestRepository):
    cls = base.Repository

    def test___init__(self):
        repo = self.create_obj(name="repo", foo="FOO", bar=None)
        assert isinstance(repo.instance, ProxyType)
        assert repo.instance == self.default_instance
        assert repo.name == "repo"
        assert repo._config_params == {"foo": "FOO"}

    def test___getattr__(self):
        instance = mock.Mock(foo="FOO")
        repo = self.create_obj(instance=instance, name="repo", bar="BAR")
        assert repo.instance == instance
        assert repo.foo == "FOO"
        assert repo.bar == "BAR"

    @pytest.mark.parametrize(
        "repo_has_issues, success",
        (
            (True, True),
            (True, False),
            (False, False),
        ),
        ids=(
            "repo-has-issues-success",
            "repo-has-issues-failure",
            "repo-issues-not-found",
        ),
    )
    @pytest.mark.parametrize(
        "needs_selector", (True, False), ids=("needs-selector", "doesnt-need-selector")
    )
    def test_get_open_issues(self, repo_has_issues, success, needs_selector):
        repo = self.create_obj()

        API_RESULT_PAGES = [[1, 2, 3], [4, 5, 6]]

        if needs_selector:
            repo._api_result_selectors = {"issues": "issues"}
            API_RESULT_PAGES = [{"issues": page} for page in API_RESULT_PAGES]

        expectation = nullcontext()
        if success:
            API_RESPONSES = [
                MockResponse(
                    status_code=requests.codes.ok,
                    json=mock.Mock(return_value=api_result_page),
                )
                for api_result_page in API_RESULT_PAGES
            ]
        else:
            if repo_has_issues:
                API_RESPONSES = [MockResponse(status_code=requests.codes.forbidden)]
                expectation = pytest.raises(requests.HTTPError)
            else:
                API_RESPONSES = [MockResponse(status_code=requests.codes.not_found)]

        with (
            mock.patch.object(repo, "get_issue_params") as get_issue_params,
            mock.patch.object(repo, "get_next_page") as get_next_page,
            mock.patch.object(repo, "normalize_issue") as normalize_issue,
            mock.patch.object(requests, "get") as requests_get,
        ):
            get_issue_params.return_value = {}
            if repo_has_issues:
                get_next_page_retvals = [
                    {"url": f"https://api.example.net?page={i + 1}"}
                    for i in range(len(API_RESULT_PAGES))
                ]
            else:
                get_next_page_retvals = [{"url": "https://api.example.net?page=1"}]
            get_next_page.side_effect = get_next_page_retvals + [None]
            requests_get.side_effect = API_RESPONSES
            normalize_issue.side_effect = lambda x: x

            with expectation:
                issues = repo.get_open_issues()

        # At least one attempt…
        assert get_next_page.call_args_list[0] == mock.call(endpoint="issues", response=None)

        if success:
            if needs_selector:
                assert issues == list(
                    chain.from_iterable(res["issues"] for res in API_RESULT_PAGES)
                )
            else:
                assert issues == list(chain.from_iterable(API_RESULT_PAGES))
            get_issue_params.assert_called_once_with()
            for response in API_RESPONSES:
                get_next_page.assert_any_call(endpoint="issues", response=response)
            assert requests_get.call_args_list == [
                mock.call(**kwargs) for kwargs in get_next_page_retvals
            ]
        else:
            get_next_page.assert_called_once()
            get_issue_params.assert_called_once_with()
