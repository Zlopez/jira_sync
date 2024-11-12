from typing import Iterator
from unittest import mock

import pytest

from jira_sync import jira_wrapper
from jira_sync.config.model import JiraConfig
from jira_sync.jira_wrapper import JiraRunMode


def pytest_generate_tests(metafunc):
    if "run_mode" in metafunc.fixturenames:
        metafunc.parametrize(
            "run_mode",
            (JiraRunMode.READ_WRITE, JiraRunMode.READ_ONLY, JiraRunMode.DRY_RUN),
            ids=("read-write", "read-only", "dry-run"),
            indirect=True,
        )


@pytest.fixture
def run_mode(request):
    return request.param


TEST_JIRA_CONFIG = {
    "instance_url": "https://jira.example.com",
    "project": "Project",
    "token": "TOKEN",
    "default_issue_type": "Story",
    "label": "label",
    "statuses": {
        "new": "NEW",
        "assigned": "IN_PROGRESS",
        "blocked": "BLOCKED",
        "closed": "DONE",
    },
}


@pytest.fixture(scope="session")
def jira_config() -> JiraConfig:
    return JiraConfig.model_validate(TEST_JIRA_CONFIG)


@pytest.fixture
def jira_params(jira_config, run_mode) -> dict[str, str | object]:
    return {"jira_config": jira_config, "run_mode": run_mode}


@pytest.fixture
def mocked_jira_pkg() -> Iterator[mock.Mock]:
    with mock.patch("jira_sync.jira_wrapper.jira") as mocked_jira:
        yield mocked_jira


@pytest.fixture
def jira_obj(jira_params, mocked_jira_pkg):
    return jira_wrapper.JIRA(**jira_params)


class TestJIRA:
    ISSUE_STATUSES = {
        "OLDSTATUS": "l1234",
        "NEWSTATUS": "abc345",
    }

    def test___init__(self, run_mode, jira_obj, jira_config, mocked_jira_pkg):
        if run_mode == JiraRunMode.DRY_RUN:
            mocked_jira_pkg.client.JIRA.assert_not_called()
            assert jira_obj._jira is None
        else:
            mocked_jira_pkg.client.JIRA.assert_called_with(
                str(jira_config.instance_url), token_auth=jira_config.token
            )
            assert jira_obj._jira == mocked_jira_pkg.client.JIRA.return_value
        assert jira_obj.jira_config == jira_config
        assert jira_obj.run_mode == run_mode

    def test_jira(self, run_mode, jira_obj, mocked_jira_pkg):
        if run_mode == JiraRunMode.DRY_RUN:
            with pytest.raises(RuntimeError, match="JIRA client object not established"):
                jira_obj.jira
        else:
            assert jira_obj.jira is mocked_jira_pkg.client.JIRA.return_value

    @pytest.mark.parametrize(
        "testcase", ("issues-found", "issues-found-inexact", "issues-not-found")
    )
    def test_get_issue_by_link(self, testcase, run_mode, jira_obj, jira_config):
        issues_found = "issues-found" in testcase
        inexact = "inexact" in testcase

        ISSUE_URL = "https://foo.bar/issue/1"

        if run_mode != JiraRunMode.DRY_RUN:
            if issues_found:
                inexact_issue = mock.Mock()
                inexact_issue.fields.description = f"{ISSUE_URL} + some garbage!\nSome\nmore\ntext."

                issues = [inexact_issue]

                if not inexact:
                    exact_issue = mock.Mock()
                    exact_issue.fields.description = f"{ISSUE_URL}\nSome\nmore\ntext."
                    issues.append(exact_issue)
            else:
                issues = []

            jira_obj.jira.search_issues.return_value = issues

        retval = jira_obj.get_issue_by_link(url=ISSUE_URL, instance="testinstance", repo="test")

        if run_mode == JiraRunMode.DRY_RUN:
            assert jira_obj._jira is None
            assert retval is None
            return

        jira_obj.jira.search_issues.assert_called_once()
        (jql_str,), _ = jira_obj.jira.search_issues.call_args

        snippets = [sn.strip() for sn in jql_str.split("AND")]

        assert f'project = "{jira_config.project}"' in snippets
        assert f'Description ~ "{ISSUE_URL}"' in snippets
        assert 'labels IN ("testinstance:test", "test")' in snippets

        if issues_found:
            if not inexact:
                assert retval is exact_issue
            else:
                assert retval is inexact_issue
        else:
            assert retval is None

    @pytest.mark.parametrize(
        "labels_as_string", (True, False), ids=("labels-as-string", "labels-as-list")
    )
    def test_get_open_issues_by_labels(self, labels_as_string, run_mode, jira_obj, jira_config):
        ISSUE_URL = "https://foo.bar/issue/1"

        if run_mode != JiraRunMode.DRY_RUN:
            issue = mock.Mock()
            issue.fields.description = f"{ISSUE_URL}\nSome\nmore\ntext."
            jira_obj.jira.search_issues.return_value = [issue]

        if labels_as_string:
            labels = "labels"
        else:
            labels = ["labels"]

        retval = jira_obj.get_open_issues_by_labels(labels=labels)

        if run_mode == JiraRunMode.DRY_RUN:
            assert jira_obj._jira is None
            assert retval == []
            return

        assert retval == [issue]

        jira_obj.jira.search_issues.assert_called_once()
        (jql_str,), _ = jira_obj.jira.search_issues.call_args

        snippets = [sn.strip() for sn in jql_str.split("AND")]

        assert f'project = "{jira_config.project}"' in snippets
        assert 'labels IN ("labels")' in snippets
        assert 'status NOT IN ("Done", "Closed")' in snippets

    @pytest.mark.parametrize(
        "test_case", ("success-labels-as-str", "success-labels-as-collection", "failure")
    )
    def test_create_issue(
        self, test_case, run_mode, mocked_jira_pkg, jira_obj, jira_params, caplog
    ):
        success = "success" in test_case
        labels_as_str = "labels-as-str" in test_case

        if run_mode != JiraRunMode.DRY_RUN:
            if success:
                jira_obj.jira.create_issue.return_value = issue_sentinel = object()
            else:
                mocked_jira_pkg.exceptions.JIRAError = RuntimeError
                jira_obj.jira.create_issue.side_effect = RuntimeError("BOO")

        if labels_as_str:
            labels = "label"
        else:
            labels = ("label",)

        with caplog.at_level("DEBUG"):
            retval = jira_obj.create_issue(
                summary="summary", description="description", url="url", labels=labels
            )

        if run_mode != JiraRunMode.READ_WRITE:
            assert run_mode != JiraRunMode.DRY_RUN or jira_obj._jira is None
            assert retval is None
            return

        if success:
            assert retval == issue_sentinel
            assert str(RuntimeError("BOO")) not in caplog.text
        else:
            assert retval is None
            assert str(RuntimeError("BOO")) in caplog.text

    @pytest.mark.parametrize("cold_cache", (True, False), ids=("cold-cache", "hot-cache"))
    def test__get_issue_transition_statuses(self, cold_cache, run_mode, jira_obj):
        issue = mock.Mock(key="JIRA-KEY")

        if run_mode == JiraRunMode.DRY_RUN:
            assert jira_obj._jira is None
            assert jira_obj._get_issue_transition_statuses(issue) == {}
            return

        with mock.patch.dict(jira_obj.project_statuses, clear=True):
            if cold_cache:
                jira_obj.jira.transitions.return_value = [
                    {"name": key, "id": value} for key, value in self.ISSUE_STATUSES.items()
                ]
            else:
                jira_obj.project_statuses[issue] = self.ISSUE_STATUSES

            assert jira_obj._get_issue_transition_statuses(issue) == self.ISSUE_STATUSES

        if cold_cache:
            jira_obj.jira.transitions.assert_called_once_with(issue)
        else:
            jira_obj.jira.transitions.assert_not_called()

    @pytest.mark.parametrize("needs_transition", (True, False), ids=("needs-transition", "noop"))
    def test_transition_issue(self, needs_transition, run_mode, jira_obj, caplog):
        issue = mock.Mock(key="KEY")
        if needs_transition:
            issue.fields.status.name = "OLDSTATUS"
        else:
            issue.fields.status.name = "NEWSTATUS"

        with caplog.at_level("DEBUG"), mock.patch.dict(jira_obj.project_statuses, clear=True):
            jira_obj.project_statuses[issue] = self.ISSUE_STATUSES
            jira_obj.transition_issue(issue, "NEWSTATUS")

        if run_mode != JiraRunMode.READ_WRITE:
            assert run_mode != JiraRunMode.DRY_RUN or jira_obj._jira is None
            return

        if needs_transition:
            assert "Changing status to 'NEWSTATUS' in ticket KEY" in caplog.text
            jira_obj.jira.transition_issue.assert_called_once_with(
                issue, self.ISSUE_STATUSES["NEWSTATUS"]
            )
        else:
            jira_obj.jira.transition_issue.assert_not_called()

    @pytest.mark.parametrize(
        "assignee_set, needs_assignment",
        (
            (False, True),
            (True, True),
            (True, False),
        ),
        ids=("assign", "reassign", "noop"),
    )
    def test_assign_to_issue(self, assignee_set, needs_assignment, run_mode, jira_obj, caplog):
        issue = mock.Mock(key="KEY")
        if not assignee_set:
            issue.fields.assignee = None
        elif needs_assignment:
            issue.fields.assignee.name = "oldname"

        if not needs_assignment:
            issue.fields.assignee.name = "newname"

        with caplog.at_level("DEBUG"):
            jira_obj.assign_to_issue(issue=issue, user="newname")

        if run_mode != JiraRunMode.READ_WRITE:
            assert run_mode != JiraRunMode.DRY_RUN or jira_obj._jira is None
            return

        if needs_assignment:
            assert "Assigning user newname to KEY" in caplog.text
            jira_obj.jira.assign_issue.assert_called_once_with(issue.id, "newname")
        else:
            assert "Assigning user newname to KEY" not in caplog.text
            jira_obj.jira.assign_issue.assert_not_called()

    @pytest.mark.parametrize("test_case", ("labels-as-str", "labels-as-collection", "noop"))
    def test_add_labels(self, test_case, run_mode, jira_obj, caplog):
        needs_labeling = "noop" not in test_case
        labels_as_str = "labels-as-str" in test_case

        issue = mock.Mock(key="KEY")
        issue.fields.labels = ["OLDLABEL"]
        if not needs_labeling:
            issue.fields.labels.append("NEWLABEL")

        if labels_as_str:
            labels = "NEWLABEL"
        else:
            labels = ["NEWLABEL"]

        with caplog.at_level("DEBUG"):
            jira_obj.add_labels(issue, labels)

        if run_mode != JiraRunMode.READ_WRITE:
            assert run_mode != JiraRunMode.DRY_RUN or jira_obj._jira is None
            return

        if needs_labeling:
            assert "KEY: Adding labels: NEWLABEL" in caplog.text
            issue.update.assert_called_once_with(update={"labels": [{"add": "NEWLABEL"}]})
        else:
            assert "KEY: Not adding any labels" in caplog.text
            issue.update.assert_not_called()
