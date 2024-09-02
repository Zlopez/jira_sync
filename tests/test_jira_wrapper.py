from typing import Iterator
from unittest import mock

import pytest

from jira_sync import jira_wrapper


def pytest_generate_tests(metafunc):
    if "dry_run" in metafunc.fixturenames:
        metafunc.parametrize("dry_run", (False, True), ids=("for-real", "dry-run"), indirect=True)


@pytest.fixture
def dry_run(request):
    return request.param


@pytest.fixture
def jira_params(dry_run) -> dict[str, str | object]:
    return {
        "url": "https://jira.example.com" if not dry_run else None,
        "token": object() if not dry_run else None,  # a sentinel
        "project": "project",
        "issue_type": "towel",
    }


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

    def test___init__(self, dry_run, jira_obj, jira_params, mocked_jira_pkg):
        if dry_run:
            mocked_jira_pkg.client.JIRA.assert_not_called()
            assert jira_obj.jira is None
        else:
            mocked_jira_pkg.client.JIRA.assert_called_with(
                jira_params["url"], token_auth=jira_params["token"]
            )
            assert jira_obj.jira == mocked_jira_pkg.client.JIRA.return_value
        assert jira_obj.project == jira_params["project"]
        assert jira_obj.issue_type == jira_params["issue_type"]

    @pytest.mark.parametrize(
        "testcase", ("issues-found", "issues-found-inexact", "issues-not-found")
    )
    def test_get_issue_by_link(self, testcase, dry_run, jira_obj, jira_params):
        issues_found = "issues-found" in testcase
        inexact = "inexact" in testcase

        ISSUE_URL = "https://foo.bar/issue/1"

        if not dry_run:
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

        retval = jira_obj.get_issue_by_link(
            url=ISSUE_URL, instance="testinstance", repo="test", title="Foo"
        )

        if dry_run:
            assert jira_obj.jira is None
            assert retval is None
            return

        jira_obj.jira.search_issues.assert_called_once()
        (jql_str,), _ = jira_obj.jira.search_issues.call_args

        snippets = [sn.strip() for sn in jql_str.split("AND")]

        jira_project = jira_params["project"]
        assert f'project = "{jira_project}"' in snippets
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
    def test_get_open_issues_by_labels(self, labels_as_string, dry_run, jira_obj, jira_params):
        ISSUE_URL = "https://foo.bar/issue/1"

        if not dry_run:
            issue = mock.Mock()
            issue.fields.description = f"{ISSUE_URL}\nSome\nmore\ntext."
            jira_obj.jira.search_issues.return_value = [issue]

        if labels_as_string:
            labels = "labels"
        else:
            labels = ["labels"]

        retval = jira_obj.get_open_issues_by_labels(labels=labels)

        if dry_run:
            assert jira_obj.jira is None
            assert retval == []
            return

        assert retval == [issue]

        jira_obj.jira.search_issues.assert_called_once()
        (jql_str,), _ = jira_obj.jira.search_issues.call_args

        snippets = [sn.strip() for sn in jql_str.split("AND")]

        jira_project = jira_params["project"]
        assert f'project = "{jira_project}"' in snippets
        assert 'labels IN ("labels")' in snippets
        assert 'status NOT IN ("Done", "Closed")' in snippets

    @pytest.mark.parametrize("success", (True, False), ids=("success", "failure"))
    def test_create_issue(self, success, dry_run, mocked_jira_pkg, jira_obj, jira_params):
        if not dry_run:
            if success:
                issue_sentinel = object()
                jira_obj.jira.create_issue.return_value = issue_sentinel = object()
            else:
                mocked_jira_pkg.exceptions.JIRAError = RuntimeError
                jira_obj.jira.create_issue.side_effect = RuntimeError("BOO")

        retval = jira_obj.create_issue(
            summary="summary", description="description", url="url", label="label"
        )

        if dry_run:
            assert jira_obj.jira is None
            assert retval is None
            return

        if success:
            assert retval == issue_sentinel
        else:
            assert retval is None

    @pytest.mark.parametrize("cold_cache", (True, False), ids=("cold-cache", "hot-cache"))
    def test__get_issue_transition_statuses(self, cold_cache, dry_run, jira_obj):
        issue_sentinel = object()

        if dry_run:
            assert jira_obj.jira is None
            assert jira_obj._get_issue_transition_statuses(issue_sentinel) == {}
            return

        with mock.patch.dict(jira_obj.project_statuses, clear=True):
            if cold_cache:
                jira_obj.jira.transitions.return_value = [
                    {"name": key, "id": value} for key, value in self.ISSUE_STATUSES.items()
                ]
            else:
                jira_obj.project_statuses[issue_sentinel] = self.ISSUE_STATUSES

            assert jira_obj._get_issue_transition_statuses(issue_sentinel) == self.ISSUE_STATUSES

        if cold_cache:
            jira_obj.jira.transitions.assert_called_once_with(issue_sentinel)
        else:
            jira_obj.jira.transitions.assert_not_called()

    @pytest.mark.parametrize("needs_transition", (True, False), ids=("needs-transition", "noop"))
    def test_transition_issue(self, needs_transition, dry_run, jira_obj, caplog):
        issue = mock.Mock(key="KEY")
        if needs_transition:
            issue.fields.status.name = "OLDSTATUS"
        else:
            issue.fields.status.name = "NEWSTATUS"

        with caplog.at_level("DEBUG"), mock.patch.dict(jira_obj.project_statuses, clear=True):
            jira_obj.project_statuses[issue] = self.ISSUE_STATUSES
            jira_obj.transition_issue(issue, "NEWSTATUS")

        if dry_run:
            assert jira_obj.jira is None
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
    def test_assign_to_issue(self, assignee_set, needs_assignment, dry_run, jira_obj, caplog):
        issue = mock.Mock(key="KEY")
        if not assignee_set:
            issue.fields.assignee = None
        elif needs_assignment:
            issue.fields.assignee.name = "oldname"

        if not needs_assignment:
            issue.fields.assignee.name = "newname"

        with caplog.at_level("DEBUG"):
            jira_obj.assign_to_issue(issue=issue, user="newname")

        if dry_run:
            assert jira_obj.jira is None
            return

        if needs_assignment:
            assert "Assigning user newname to KEY" in caplog.text
            jira_obj.jira.assign_issue.assert_called_once_with(issue.id, "newname")
        else:
            assert "Assigning user newname to KEY" not in caplog.text
            jira_obj.jira.assign_issue.assert_not_called()

    @pytest.mark.parametrize("needs_labeling", (True, False), ids=("needs-labeling", "noop"))
    def test_add_label(self, needs_labeling, dry_run, jira_obj, caplog):
        issue = mock.Mock(key="KEY")
        issue.fields.labels = ["OLDLABEL"]
        if not needs_labeling:
            issue.fields.labels.append("NEWLABEL")

        with caplog.at_level("DEBUG"):
            jira_obj.add_label(issue, "NEWLABEL")

        if dry_run:
            assert jira_obj.jira is None
            return

        if needs_labeling:
            assert "Adding label NEWLABEL to KEY" in caplog.text
            issue.add_field_value.assert_called_once_with("labels", "NEWLABEL")
        else:
            assert "Adding label NEWLABEL to KEY" not in caplog.text
            issue.add_field_value.assert_not_called()
