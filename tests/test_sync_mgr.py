from functools import partial
from pathlib import Path
from unittest import mock

import pytest

from jira_sync.config import Config
from jira_sync.jira_wrapper import JiraRunMode
from jira_sync.repositories import Instance, Repository
from jira_sync.repositories import Issue as ForgeIssue
from jira_sync.repositories import IssueStatus as ForgeIssueStatus
from jira_sync.sync_mgr import SyncManager

from .common import (
    JiraIssue,
    gen_test_config,
    mock_jira__create_issue,
    mock_jira__get_issues_by_labels,
    mock_requests_get,
)


def mock_with_name(*args, cls: type = mock.Mock, name: str, **kwargs):
    mock_obj = cls(*args, **kwargs)
    mock_obj.name = name
    return mock_obj


@pytest.fixture(autouse=True)
def intercept_requests():
    with (
        mock.patch("requests.get") as get,
        mock.patch("requests.post") as post,
        mock.patch("requests.put") as put,
        mock.patch("requests.delete") as delete,
    ):
        get.side_effect = mock_requests_get
        post.side_effect = RuntimeError
        put.side_effect = RuntimeError
        delete.side_effect = RuntimeError

        yield


@pytest.fixture
def mock_instance():
    patches = []

    def mock_instance_from_config(**kwargs):
        real_instance = Instance.from_config(**kwargs)
        mocked_instance = mock_with_name(
            wraps=real_instance,
            name=real_instance.name,
            enabled=real_instance.enabled,
        )

        patch = mock.patch.dict(real_instance.repositories)
        patches.append(patch)
        patch.start()

        for name, repo in real_instance.repositories.items():
            real_instance.repositories[name] = mock_with_name(
                wraps=repo,
                name=repo.name,
                enabled=repo.enabled,
            )

        return mocked_instance

    with mock.patch("jira_sync.sync_mgr.Instance") as MockInstance:
        MockInstance.from_config.side_effect = mock_instance_from_config
        yield

    for patch in patches:
        patch.stop()


@pytest.fixture
def mock_jira():
    jira = mock.Mock()
    jira.create_issue.side_effect = mock.Mock(wraps=partial(mock_jira__create_issue, {}))
    jira.get_issues_by_labels.side_effect = mock.Mock(wraps=mock_jira__get_issues_by_labels)

    with mock.patch("jira_sync.sync_mgr.JIRA") as JIRA:
        JIRA.return_value = jira
        yield jira


@pytest.fixture
def test_config(request: pytest.FixtureRequest) -> Config:
    params = {"instances_enabled": True, "repositories_enabled": True}
    if hasattr(request, "param"):
        params |= request.param

    return Config.model_validate(gen_test_config(**params) | {"config_path": Path("/dev/null")})


@pytest.fixture
def sync_mgr(test_config, mock_jira, mock_instance):
    return SyncManager(config=test_config, run_mode=JiraRunMode.READ_WRITE)


class TestSyncManager:
    def test___init__(self, sync_mgr, test_config, mock_jira, mock_instance):
        assert sync_mgr._config == test_config
        assert sync_mgr._jira_config == (jira_config := test_config.general.jira)
        assert sync_mgr._jira_statuses == jira_config.statuses
        assert all(isinstance(status, str) for status in sync_mgr._jira_status_values)
        assert sync_mgr._jira is mock_jira

        assert all(
            isinstance(key, str) and isinstance(value._mock_wraps, Instance)
            for key, value in sync_mgr._instances_by_name.items()
        )

    def test_sync_issues(self, sync_mgr):
        with (
            mock.patch.object(
                sync_mgr, "filter_open_jira_issues_by_forge_repo"
            ) as filter_open_jira_issues_by_forge_repo,
            mock.patch.object(sync_mgr, "retrieve_open_jira_issues") as retrieve_open_jira_issues,
            mock.patch.object(sync_mgr, "retrieve_forge_issues") as retrieve_forge_issues,
            mock.patch.object(sync_mgr, "match_jira_forge_issues") as match_jira_forge_issues,
            mock.patch.object(sync_mgr, "close_jira_issues") as close_jira_issues,
            mock.patch.object(
                sync_mgr, "create_or_reopen_jira_issues"
            ) as create_or_reopen_jira_issues,
            mock.patch.object(
                sync_mgr, "reconcile_jira_forge_issues"
            ) as reconcile_jira_forge_issues,
        ):
            retrieve_open_jira_issues.return_value = open_jira_issues = object()
            filter_open_jira_issues_by_forge_repo.return_value = filtered_jira_issues = list()
            retrieve_forge_issues.return_value = forge_issues = object()
            matched_issues = {object()}
            unmatched_jira_issues = {object()}
            unmatched_forge_issues = {object()}
            match_jira_forge_issues.return_value = (
                matched_issues,
                unmatched_jira_issues,
                unmatched_forge_issues,
            )
            create_or_reopen_jira_issues.return_value = matched_created_or_reopened_issues = {
                object()
            }

            sync_mgr.sync_issues()

        retrieve_open_jira_issues.assert_called_once_with()
        filter_open_jira_issues_by_forge_repo.assert_called_once_with(open_jira_issues)
        retrieve_forge_issues.assert_called_once_with()
        match_jira_forge_issues.assert_called_once_with(filtered_jira_issues, forge_issues)
        close_jira_issues.assert_called_once_with(unmatched_jira_issues)
        create_or_reopen_jira_issues.assert_called_once_with(unmatched_forge_issues)
        reconcile_jira_forge_issues.assert_called_once_with(
            matched_issues | matched_created_or_reopened_issues
        )

    def test_retrieve_open_jira_issues(self, sync_mgr, mock_jira, test_config):
        sync_mgr.retrieve_open_jira_issues()

        mock_jira.get_issues_by_labels.assert_called_once_with(test_config.general.jira.label)

    @pytest.mark.parametrize(
        "test_config, repos_enabled",
        (
            ({"repositories_enabled": True}, True),
            ({"repositories_enabled": False}, False),
        ),
        ids=("repositories-enabled", "repositories-disabled"),
        indirect=["test_config"],
    )
    def test_retrieve_forge_issues(self, repos_enabled, sync_mgr, mock_jira, test_config, caplog):
        with caplog.at_level("DEBUG"):
            issues = sync_mgr.retrieve_forge_issues()

        if repos_enabled:
            for instance in sync_mgr._instances_by_name.values():
                for repo in instance.repositories.values():
                    repo.get_issues.assert_has_calls([mock.call(), mock.call(closed=True)])
                    assert f"Querying repository {instance.name}:{repo.name}…" in caplog.text

            for inst_name, inst_spec in test_config.instances.items():
                # This doesn’t check queried repositories.
                for repo_name in inst_spec.repositories:
                    assert any(
                        issue.repository.name == repo_name
                        and issue.repository.instance.name == inst_name
                        for issue in issues
                    )
        else:
            assert not issues

            for instance in sync_mgr._instances_by_name.values():
                for repo in instance.repositories.values():
                    repo.get_issues.assert_not_called()
                    assert f"Querying repository {instance.name}:{repo.name}…" not in caplog.text

    def test_jira_repo_labels(self, sync_mgr, test_config):
        labels = sync_mgr.jira_repo_labels

        expected_labels = {
            f"{inst_name}:{repo_name}"
            for inst_name, inst_spec in test_config.instances.items()
            for repo_name in inst_spec.repositories
        }

        assert labels == expected_labels

    def test_filter_open_jira_issues_by_forge_repo(self, sync_mgr):
        sync_mgr.jira_repo_labels = repo_labels = ("1", "3", "5")
        jira_issues = [
            mock.Mock(fields=mock.Mock(labels=("ignore", "me", str(idx)))) for idx in range(6)
        ]

        filtered_jira_issues = sync_mgr.filter_open_jira_issues_by_forge_repo(jira_issues)

        assert len(filtered_jira_issues) == len(repo_labels)
        assert all(
            any(repo_label in issue.fields.labels for repo_label in repo_labels)
            for issue in filtered_jira_issues
        )

    @pytest.mark.parametrize("test_case", ("single-line", None))
    def test_get_full_url_from_jira_issue(self, test_case, sync_mgr):
        if not test_case:
            expected_url = None
        else:
            expected_url = "URL"
        issue = mock.Mock(fields=mock.Mock(external_url=expected_url))

        url = sync_mgr.get_full_url_from_jira_issue(issue)

        assert url == expected_url

    def test_match_jira_forge_issues(self, sync_mgr):
        jira_issues = [mock.Mock(fields=mock.Mock(external_url=f"URL{idx}")) for idx in range(10)]

        # Set external URL field
        forge_issues = [mock.Mock(full_url=f"URL{idx}") for idx in range(5, 15)]

        matched_issues, unmatched_jira_issues, unmatched_forge_issues = (
            sync_mgr.match_jira_forge_issues(jira_issues, forge_issues)
        )

        matched_urls = {f"URL{idx}" for idx in range(5, 10)}
        unmatched_urls = {f"URL{idx}" for idx in range(15) if not 5 <= idx < 10}

        assert all(
            jira_issue.fields.external_url == forge_issue.full_url
            and forge_issue.full_url in matched_urls
            for jira_issue, forge_issue in matched_issues
        )

        assert all(
            any(jira_issue.fields.external_url == url for url in unmatched_urls)
            for jira_issue in unmatched_jira_issues
        )

        assert all(forge_issue.full_url in unmatched_urls for forge_issue in unmatched_forge_issues)

    def test_close_jira_issues(self, sync_mgr, caplog):
        jira_issues = [mock.Mock(key="JIRA-001"), mock.Mock(key="JIRA-002")]

        with caplog.at_level("DEBUG"):
            sync_mgr.close_jira_issues(jira_issues)

        assert "Closing 2 JIRA issues: JIRA-001, JIRA-002" in caplog.text
        sync_mgr._jira.transition_issue.assert_has_calls(
            [mock.call(jira_issue, sync_mgr._jira_statuses.closed) for jira_issue in jira_issues],
            any_order=True,
        )

    @pytest.mark.parametrize("test_case", ("normal", "creation-fails", "no-forge-issues"))
    def test_create_or_reopen_jira_issues(self, test_case, sync_mgr, caplog):
        if "no-forge-issues" in test_case:
            forge_issues = []
        else:
            instance = mock.Mock()
            instance.name = "instance.io"
            repository = mock.Mock(instance=instance)
            repository.name = "repository"
            forge_issues = [
                mock.Mock(
                    full_url=f"URL{idx}",
                    title=f"Title {idx}",
                    content="Some content",
                    repository=repository,
                    story_points=10,
                )
                for idx in range(2)
            ]

        closed_jira_issue = mock.Mock(fields=mock.Mock(external_url="URL0"))
        sync_mgr._jira.get_issues_by_labels.side_effect = None
        sync_mgr._jira.get_issues_by_labels.return_value = [closed_jira_issue]

        with (
            mock.patch.object(
                sync_mgr, "match_jira_forge_issues", wraps=sync_mgr.match_jira_forge_issues
            ) as mock_match_jira_forge_issues,
            mock.patch.object(sync_mgr._jira, "create_issue") as mock_jira_create_issue,
            caplog.at_level("DEBUG"),
        ):
            if "creation-fails" in test_case:
                mock_jira_create_issue.return_value = None
            else:
                mock_jira_create_issue.return_value = created_sentinel = object()

            matched_issues = sync_mgr.create_or_reopen_jira_issues(forge_issues)

        if test_case == "no-forge-issues":
            assert matched_issues == set()
            sync_mgr._jira.get_issues_by_labels.assert_not_called()
            assert "No JIRA issues to create or reopen." in caplog.text
            assert "Creating/reopening JIRA issues for unmatched forge issues" not in caplog.text
            return

        assert (closed_jira_issue, forge_issues[0]) in matched_issues

        if "creation-fails" in test_case:
            assert len(forge_issues) - 1 == len(matched_issues)
            assert "Couldn’t create new JIRA issue from 'URL1'" in caplog.text
        else:
            assert len(forge_issues) == len(matched_issues)
            assert "Couldn’t create new JIRA issue from" not in caplog.text
            ((created_jira_issue, forge_issue),) = list(
                matched_issues - {(closed_jira_issue, forge_issues[0])}
            )
            assert created_jira_issue is created_sentinel
            assert forge_issue is forge_issues[1]

        assert "Creating/reopening JIRA issues for unmatched forge issues" in caplog.text
        sync_mgr._jira.get_issues_by_labels.assert_called_once_with(
            sync_mgr._jira_config.label, ["URL0", "URL1"], closed=True
        )
        mock_match_jira_forge_issues.assert_called_once_with(
            sync_mgr._jira.get_issues_by_labels.return_value, forge_issues
        )
        mock_jira_create_issue.assert_called_once_with(
            url="URL1",
            labels=[
                sync_mgr._jira_config.label,
                "instance.io:repository",
                "instance.io",
                "repository",
            ],
        )

    @pytest.mark.parametrize("testcase", ("usermap-key", "usermap-email"))
    def test_reconcile_jira_forge_issues(self, testcase, sync_mgr, caplog):
        jira_issue_specs = [
            {},
            {
                "fields": {
                    "assignee": {"key": "jira_user", "emailAddress": "jira_user@example.com"},
                    "status": {"name": "IN_PROGRESS"},
                    "story_points": 0,
                },
            },
            {"fields": {"assignee": {}, "status": {"name": "IN_PROGRESS"}}},
            {
                "fields": {
                    "assignee": {"key": "jira_user", "emailAddress": "jira_user@example.com"},
                    "status": {"name": "IN_PROGRESS"},
                    "story_points": 0,
                },
            },
            {"fields": {"status": {"name": "SITUATION_IS_BORF"}}},
        ]
        forge_issue_specs = [
            {"assignee": "forge_user", "status": ForgeIssueStatus.assigned},
            {"assignee": "forge_user", "status": ForgeIssueStatus.assigned},
            {"assignee": "other_forge_user", "status": ForgeIssueStatus.assigned},
            {},
            {},
        ]

        jira_issues = [
            JiraIssue.model_validate({"key": f"JIRA-{num:04}"} | spec)
            for num, spec in enumerate(jira_issue_specs, 1)
        ]

        if "usermap-email" in testcase:
            mapped_jira_user = "jira_user@example.com"
        else:
            mapped_jira_user = "jira_user"

        mock_repo = mock_with_name(
            Repository,
            name="repo",
            instance=mock_with_name(name="instance"),
            usermap={"forge_user": mapped_jira_user},
        )
        forge_issues = [
            ForgeIssue(
                **(
                    {
                        "repository": mock_repo,
                        "full_url": f"https://forge/repo/issues/{num}",
                        "title": f"Title {num}",
                        "content": f"Content {num}",
                        "assignee": None,
                        "status": ForgeIssueStatus.new,
                        "story_points": 10,
                    }
                    | spec
                )
            )
            for num, spec in enumerate(forge_issue_specs, 1)
        ]

        matched_issues = list(zip(jira_issues, forge_issues, strict=True))

        with (
            mock.patch.object(sync_mgr._jira, "assign_to_issue") as assign_to_issue,
            mock.patch.object(sync_mgr._jira, "transition_issue") as transition_issue,
            mock.patch.object(sync_mgr._jira, "add_labels") as add_labels,
            mock.patch.object(sync_mgr._jira, "add_story_points") as add_story_points,
            mock.patch.object(sync_mgr._jira, "update_issue") as update_issue,
            caplog.at_level("DEBUG"),
        ):
            sync_mgr.reconcile_jira_forge_issues(matched_issues)

        # Check user assignments
        assert assign_to_issue.call_args_list == [
            mock.call(jira_issues[0], mapped_jira_user),
            mock.call(jira_issues[2], None),
            mock.call(jira_issues[3], None),
        ]
        # Change unassigned to known forge <=> JIRA user
        assert f"JIRA-0001: Changing assignee from None to '{mapped_jira_user}'" in caplog.text
        # Keep known forge <=> JIRA user
        assert (
            "JIRA-0002: Not changing assignee from 'jira_user <jira_user@example.com>' to"
            + f" '{mapped_jira_user}'"
            in caplog.text
        )
        # Unassign JIRA user without corresponding forge user
        assert "JIRA-0003: Changing assignee from 'other_jira_user' to None" in caplog.text
        # Unassign JIRA user when forge issue is unassigned
        assert "JIRA-0004: Changing assignee from 'jira_user' to None" in caplog.text
        # Leave issue unassigned
        assert "JIRA-0005: Not assigning to None" in caplog.text

        # Check state transitions & set labels
        assert transition_issue.call_args_list == [
            mock.call(jira_issues[0], "IN_PROGRESS"),
            mock.call(jira_issues[3], "NEW"),
        ]
        assert add_labels.call_args_list == [
            mock.call(jira_issues[0], (sync_mgr._jira_config.label, "instance:repo"), {}),
            mock.call(jira_issues[1], (sync_mgr._jira_config.label, "instance:repo"), {}),
            mock.call(jira_issues[2], (sync_mgr._jira_config.label, "instance:repo"), {}),
            mock.call(jira_issues[3], (sync_mgr._jira_config.label, "instance:repo"), {}),
            mock.call(jira_issues[4], (sync_mgr._jira_config.label, "instance:repo"), {}),
        ]
        assert add_story_points.call_args_list == [
            mock.call(jira_issues[0], 10, mock.ANY),
            mock.call(jira_issues[1], 10, mock.ANY),
            mock.call(jira_issues[2], 10, mock.ANY),
            mock.call(jira_issues[3], 10, mock.ANY),
            mock.call(jira_issues[4], 10, mock.ANY),
        ]
        assert update_issue.call_count == 5
        assert "JIRA-0001: Transitioning issue from NEW to IN_PROGRESS" in caplog.text
        assert "JIRA-0002: Not transitioning issue with status IN_PROGRESS" in caplog.text
        assert "JIRA-0003: Not transitioning issue with status IN_PROGRESS" in caplog.text
        assert "JIRA-0004: Transitioning issue from IN_PROGRESS to NEW" in caplog.text
        assert "JIRA-0005: Not transitioning status from SITUATION_IS_BORF to NEW" in caplog.text
