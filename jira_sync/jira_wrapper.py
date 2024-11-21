"""
Wrapper around the JIRA API.

See https://developer.atlassian.com/server/jira/platform/rest-apis/
"""

import logging
from collections.abc import Collection
from typing import cast

import jira

# Help out mypy, it trips over directly using jira.resources.Issue.
from jira import Issue

from .config.model import JiraConfig

log = logging.getLogger(__name__)


class JIRA:
    """
    Class for interacting with the JIRA API.

    :attribute jira_config: Configuration of the JIRA instance
    :attribute jira: Instance of the jira.JIRA object
    :attribute project_statuses: List of statuses on the project in the format
                                 {"status": "id"}.
                                 Example: {"NEW": "1"}
    """

    jira_config: JiraConfig
    jira: jira.client.JIRA | None
    project_statuses: dict[Issue, dict[str, str]]

    def __init__(self, jira_config: JiraConfig, dry_run: bool = False):
        """
        Initialize the JIRA object.

        :param jira_config: The configuration describing the JIRA instance
        :param dry_run: Whether to connect to the JIRA instance or not
        """
        self.jira_config = jira_config

        if dry_run:
            self.jira = None
        else:
            self.jira = jira.client.JIRA(
                str(jira_config.instance_url), token_auth=jira_config.token
            )

        self.project_statuses = {}

    def get_issue_by_link(self, *, url: str, instance: str, repo: str) -> Issue | None:
        """
        Retrieve the issue with its external issue URL set to url.

        :param url: URL to search for
        :param instance: Instance name
        :param repo: Project namespace/name

        :return: Retrieved issue or None
        """
        if not self.jira:
            return None

        issues = cast(
            jira.client.ResultList[Issue],
            self.jira.search_issues(
                f'project = "{self.jira_config.project}" AND Description ~ "{url}"'
                + f' AND labels IN ("{instance}:{repo}", "{repo}")'
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

    def get_open_issues_by_labels(self, labels: str | Collection[str]) -> list[Issue]:
        """
        Retrieve open issues for the specified label.

        :param labels: Labels to retrieve the issues by

        :return: List of issues
        """
        if not self.jira:
            return []

        if isinstance(labels, str):
            labels = [labels]

        labels_str = ", ".join(f'"{label}"' for label in labels)

        issues = cast(
            jira.client.ResultList[Issue],
            self.jira.search_issues(
                f'project = "{self.jira_config.project}" AND labels IN ({labels_str})'
                + ' AND status NOT IN ("Done", "Closed")',
                maxResults=0,
            ),
        )
        return issues

    def create_issue(
        self,
        *,
        summary: str,
        description: str,
        url: str,
        labels: Collection[str] | str | None = None,
    ) -> Issue | None:
        """
        Create a new issue in JIRA.

        :param summary: Name of the ticket
        :param description: Description of the ticket
        :param url: URL to add to url field, it is also added to description
        :param labels: Label(s) for the issue, if any

        :return: Issue object or None
        """
        if not self.jira:
            return None

        if isinstance(labels, str):
            labels = (labels,)

        issue_dict = {
            "project": {"key": self.jira_config.project},
            "summary": summary,
            "description": url + "\n\n" + description,
            "issuetype": {"name": self.jira_config.default_issue_type},
            "labels": labels if labels else [],
        }
        try:
            return self.jira.create_issue(fields=issue_dict)
        except jira.exceptions.JIRAError as e:
            log.warning("Can’t create issue: %s", e)
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

    def add_labels(self, issue: Issue, labels: Collection[str] | str) -> None:
        """
        Add label to an issue.

        :param issue: Issue object
        :param labels: Label(s) to add
        """
        if not self.jira:
            log.info("%s: Skipping adding labels %s to JIRA issue", issue.key, ", ".join(labels))
            return

        if isinstance(labels, str):
            labels = (labels,)

        labels_to_add = [label for label in labels if label not in issue.fields.labels]

        if not labels_to_add:
            log.debug("%s: Not adding any labels", issue.key)
            return

        log.debug("%s: Adding labels: %s", issue.key, ", ".join(labels_to_add))
        labels_field_ops = [{"add": label} for label in labels_to_add]
        issue.update(update={"labels": labels_field_ops})
