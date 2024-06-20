from unittest import mock

import pytest

from jira_sync import pagure


@pytest.fixture
def pagure_url() -> str:
    return "https://pagure.io"


@pytest.fixture
def obj(pagure_url) -> pagure.Pagure:
    """Create a Pagure wrapper object."""
    return pagure.Pagure(pagure_url + "/")


class TestPagure:
    def test___init__(self, obj, pagure_url):
        assert obj.instance_url == pagure_url

    @pytest.mark.parametrize(
        "with_label, with_error",
        (
            (False, False),
            (True, False),
            (False, True),
        ),
        ids=("without-label", "with-label", "error"),
    )
    def test_get_open_project_issues(self, with_label, with_error, obj, pagure_url, caplog):
        label = "blocked" if with_label else ""

        with mock.patch.object(obj, "_get_json") as _get_json, caplog.at_level("DEBUG"):
            _get_json_side_effect = [
                {"issues": [{"issue": 1}], "pagination": {"next": "foo"}},
                {"issues": [{"issue": 2}], "pagination": {"next": None}},
            ]

            if with_error:
                _get_json_side_effect.insert(1, {})

            _get_json.side_effect = _get_json_side_effect

            issues = obj.get_open_project_issues(repo="test", label=label)

        assert issues == [{"issue": 1}, {"issue": 2}]

        first_call_args, _ = _get_json.call_args_list[0]

        assert f"{pagure_url}/api/0/test/issues" in first_call_args[0]
        if with_label:
            assert "tags=blocked" in first_call_args[0]
        else:
            assert "tags=blocked" not in first_call_args[0]

        assert "Retrieved 2 open issues from test" in caplog.text

    @pytest.mark.parametrize("success", (True, False), ids=("success", "failure"))
    def test_get_json(self, success, obj, pagure_url, caplog):
        with mock.patch("jira_sync.pagure.requests") as requests:
            requests.codes.ok = 200
            requests.codes.request.i_am_a_teapot = 418

            status_code = requests.codes.ok if success else requests.codes.request.i_am_a_teapot

            request = mock.Mock(status_code=status_code)
            request.json.return_value = json_sentinel = object()

            requests.get.return_value = request

            retval = obj._get_json(pagure_url)

        requests.get.assert_called_once_with(pagure_url)
        if success:
            assert retval == json_sentinel
            request.json.assert_called_once_with()
            assert "Error happened during retrieval" not in caplog.text
        else:
            assert retval == {}
            request.json.assert_not_called()
            assert "Error happened during retrieval" in caplog.text
