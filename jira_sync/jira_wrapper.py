"""
Wrapper around the JIRA API.

See https://developer.atlassian.com/server/jira/platform/rest-apis/
"""

import logging
import re
from typing import cast

import jira

log = logging.getLogger(__name__)


class JIRA:
    """
    Class for interacting with JIRA API.

    Attributes:
      jira: Instance of the jira.JIRA object
      project: Project to work with
      issue_type: Default issue type for creating issues
      project_states: List of states on the project in format {"state": "id"}.
                      Example: {"NEW": "1"}
    """

    jira: jira.client.JIRA
    project: str
    issue_type: str
    project_states: dict[jira.resources.Issue, dict[str, str]]

    def __init__(
        self,
        url: str,
        token: str,
        project: str,
        issue_type: str,
    ):
        """
        Class constructor.

        Params:
          url: URL to JIRA server
          token: Token to use for authentication
          project: Project to work with
          issue_type: Default issue type for creating issues
        """
        self.jira = jira.client.JIRA(url, token_auth=token)
        self.project = project
        self.issue_type = issue_type
        self.project_states = {}

    def get_issue_by_link(self, url: str, repo: str, title: str) -> jira.resources.Issue | None:
        """
        Return issue that has external issue URL set to url.

        Params:
          url: URL to search for
          repo: Project namespace/name
          title: Title of the ticket

        Returns:
          Retrieved issue or None.
        """
        # Replace special characters
        title = title.replace("[", "\\\\[")
        title = title.replace("]", "\\\\]")
        issues = cast(
            jira.client.ResultList[jira.resources.Issue],
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
            if re.match(r"^" + url + "$", issue.fields.description.split("\n")[0]):
                # Found the exact issue, let's return it
                return issue

        return issues[0]

    def get_open_issues_by_label(self, label: str) -> list[jira.resources.Issue]:
        """
        Retrieve open issues for the specified label.

        Params:
          label: Label to retrieve the issues by.

        Returns:
          List of issues.
        """
        issues = cast(
            jira.client.ResultList[jira.resources.Issue],
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
    ) -> jira.resources.Issue | None:
        """
        Create new issue in JIRA.

        Params:
          summary: Name of the ticket
          description: Description of the ticket
          url: URL to add to url field, it is also added to description
          label: Label for the issue

        Returns:
          Issue object or None.
        """
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

    def _get_issue_transition_states(self, issue: jira.resources.Issue) -> dict[str, str]:
        """
        Retrieve and cache possible ticket transition states.

        Params:
          issue: Issue object
        Returns: A dictionary mapping state names to ids
        """
        if issue not in self.project_states:
            self.project_states[issue] = {
                transition["name"]: transition["id"] for transition in self.jira.transitions(issue)
            }
        return self.project_states[issue]

    def transition_issue(self, issue: jira.resources.Issue, state: str) -> None:
        """
        Transition ticket to a new state.

        Params:
          issue: Issue object
          state: New state to move to
        """
        if issue.fields.status.name != state:
            log.debug("Changing status to '%s' in ticket %s", state, issue.key)
            self.jira.transition_issue(issue, self._get_issue_transition_states(issue)[state])

    def assign_to_issue(self, issue: jira.resources.Issue, user: str) -> None:
        """
        Assign user to ticket.

        Params:
          issue: Issue object
          user: Username to assign to ticket
        """
        if user != getattr(issue.fields.assignee, "name", None):
            log.debug("Assigning user %s to %s", user, issue.key)
            self.jira.assign_issue(issue.id, user)

    def add_label(self, issue: jira.resources.Issue, label: str) -> None:
        """
        Add label to ticket.

        Params:
          issue: Issue object
          label: Label to add
        """
        if label not in issue.fields.labels:
            log.debug("Adding label %s to %s", label, issue.key)
            issue.add_field_value("labels", label)
