"""
Wrapper around the JIRA API.

See https://developer.atlassian.com/server/jira/platform/rest-apis/
"""

import logging
from typing import cast

import jira

# Help out mypy, it trips over directly using jira.resources.Issue.
from jira import Issue

log = logging.getLogger(__name__)


class JIRA:
    """
    Class for interacting with the JIRA API.

    :attribute jira: Instance of the jira.JIRA object
    :attribute project: Project to work with
    :attribute issue_type: Default issue type for creating issues
    :attribute project_statuses: List of statuses on the project in the format
                                 {"status": "id"}.
                                 Example: {"NEW": "1"}
    """

    jira: jira.client.JIRA | None
    project: str
    issue_type: str
    project_statuses: dict[Issue, dict[str, str]]

    def __init__(
        self,
        url: str | None,
        token: str | None,
        project: str,
        issue_type: str,
    ):
        """
        Object constructor.

        Set url and token to None for dry-run (simulated) operation.

        :param url: URL to JIRA server
        :param token: Token to use for authentication
        :param project: Project to work with
        :param issue_type: Default issue type for creating issues
        """
        if url and token:
            self.jira = jira.client.JIRA(url, token_auth=token)
        else:
            self.jira = None
        self.project = project
        self.issue_type = issue_type
        self.project_statuses = {}

    def get_issue_by_link(self, url: str, repo: str, title: str) -> Issue | None:
        """
        Retrieve the issue with its external issue URL set to url.

        :param url: URL to search for
        :param repo: Project namespace/name
        :param title: Title of the ticket

        :return: Retrieved issue or None
        """
        if not self.jira:
            return None

        # Replace special characters
        title = title.replace("[", "\\\\[")
        title = title.replace("]", "\\\\]")

        issues = cast(
            jira.client.ResultList[Issue],
            self.jira.search_issues(
                (
                    "project = "
                    + self.project
                    + ' AND Description ~ "'
                    + url
                    + '" AND labels = "'
                    + repo
                    + '"'
                )
            ),
        )

        if not issues:
            return None

        # Only the exact match is correct
        for issue in issues:
            if issue.fields.description and url == issue.fields.description.split("\n")[0]:
                # Found the exact issue, let's return it
                return issue

        return issues[0]

    def get_open_issues_by_label(self, label: str) -> list[Issue]:
        """
        Retrieve open issues for the specified label.

        :param label: Label to retrieve the issues by

        :return: List of issues
        """
        if not self.jira:
            return []

        issues = cast(
            jira.client.ResultList[Issue],
            self.jira.search_issues(
                "project = "
                + self.project
                + ' AND labels = "'
                + label
                + '"'
                + " AND status not in (Done, Closed)",
                maxResults=0,
            ),
        )
        return issues

    def create_issue(
        self, summary: str, description: str, url: str, label: str = ""
    ) -> Issue | None:
        """
        Create a new issue in JIRA.

        :param summary: Name of the ticket
        :param description: Description of the ticket
        :param url: URL to add to url field, it is also added to description
        :param label: Label for the issue

        :return: Issue object or None
        """
        if not self.jira:
            return None

        issue_dict = {
            "project": {"key": self.project},
            "summary": summary,
            "description": url + "\n\n" + description,
            "issuetype": {"name": self.issue_type},
            "labels": [label] if label else [],
        }
        try:
            return self.jira.create_issue(fields=issue_dict)
        except jira.exceptions.JIRAError:
            return None

    def _get_issue_transition_statuses(self, issue: Issue) -> dict[str, str]:
        """
        Retrieve and cache possible ticket transition statuses.

        :param issue: Issue object

        :return: A dictionary mapping status names to ids
        """
        if not self.jira:
            return {}

        if issue not in self.project_statuses:
            self.project_statuses[issue] = {
                transition["name"]: transition["id"] for transition in self.jira.transitions(issue)
            }
        return self.project_statuses[issue]

    def transition_issue(self, issue: Issue, status: str) -> None:
        """
        Transition ticket to a new status.

        :param issue: Issue object
        :param status: New status to move to
        """
        if not self.jira:
            return

        if issue.fields.status.name != status:
            log.debug("Changing status to '%s' in ticket %s", status, issue.key)
            self.jira.transition_issue(issue, self._get_issue_transition_statuses(issue)[status])

    def assign_to_issue(self, issue: Issue, user: str | None) -> None:
        """
        Assign user to an issue.

        :param issue: Issue object
        :param user: Username to assign to ticket
        """
        if not self.jira:
            return

        if user != getattr(issue.fields.assignee, "name", None):
            log.debug("Assigning user %s to %s", user, issue.key)
            self.jira.assign_issue(issue.id, user)

    def add_label(self, issue: Issue, label: str) -> None:
        """
        Add label to an issue.

        :param issue: Issue object
        :param label: Label to add
        """
        if not self.jira:
            return

        if label not in issue.fields.labels:
            log.debug("Adding label %s to %s", label, issue.key)
            issue.add_field_value("labels", label)
