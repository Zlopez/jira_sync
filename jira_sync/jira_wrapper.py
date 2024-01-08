"""
Wrapper around the JIRA API.

See https://developer.atlassian.com/server/jira/platform/rest-apis/
"""
import logging
import re
from typing import cast, Dict, List, Optional

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
    project_states: Dict[str, str] = {}

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

    def get_issue_by_link(
            self, url: str, repo: str, title: str
    ) -> Optional[jira.resources.Issue]:
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
                    'project = ' + self.project +
                    ' AND Description ~ \"' + url + '\" AND labels = ' + repo
                )
            )
        )

        if not issues:
            return None

        if len(issues) > 1:
            for issue in issues:
                if re.match(
                        r"^" + url + "$",
                        issue.fields.description.split("\n")[0]
                ):
                    # Found the exact issue, let's return it
                    return issue

        return issues[0]

    def get_open_issues_by_label(
            self,
            label: str
    ) -> List[jira.resources.Issue]:
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
                (
                    'project = ' + self.project +
                    ' AND labels = ' + label +
                    ' AND status not in (Done, Closed)',
                ),
                maxResults=0
            )
        )
        return issues

    def create_issue(
            self,
            summary: str,
            description: str,
            url: str,
            label: str = ""
    ) -> Optional[jira.resources.Issue]:
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
        issue = self.jira.create_issue(fields=issue_dict)

        if issue:
            return issue

        return None

    def transition_issue(
            self, issue: jira.resources.Issue, state: str
    ) -> None:
        """
        Transition ticket to a new state.

        Params:
          issue: Issue object
          state: New state to move to
        """
        ticket_state = state
        if not self.project_states:
            transitions = self.jira.transitions(issue)
            for transition in transitions:
                self.project_states[transition["name"]] = transition["id"]

        if issue.fields.status.name != ticket_state:
            log.debug(
                "Changing status to '{}' in ticket {}".format(
                    ticket_state,
                    issue.key
                )
            )
            self.jira.transition_issue(
                issue,
                self.project_states[ticket_state]
            )

    def assign_to_issue(self, issue: jira.resources.Issue, user: str) -> None:
        """
        Assign user to ticket.

        Params:
          issue: Issue object
          user: Username to assign to ticket
        """
        # If there is no assigned user in JIRA, but there should be
        if user and not issue.fields.assignee:
            log.debug(
                "Assigning user {} to {}".format(
                    user,
                    issue.key
                )
            )
            self.jira.assign_issue(issue.id, user)
        # If user is different then the assigned user
        elif issue.fields.assignee and user != issue.fields.assignee.name:
            log.debug(
                "Assigning user {} to {}".format(
                    user,
                    issue.key
                )
            )
            self.jira.assign_issue(issue.id, user)

    def add_label(self, issue: jira.resources.Issue, label: str) -> None:
        """
        Add label to ticket.

        Params:
          issue: Issue object
          label: Label to add
        """
        if label not in issue.fields.labels:
            log.debug("Adding label {} to {}".format(label, issue.key))
            issue.add_field_value("labels", label)
