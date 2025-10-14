"""
Wrapper around the JIRA API.

See https://developer.atlassian.com/server/jira/platform/rest-apis/
"""

import logging
from collections.abc import Collection
from enum import IntEnum
from typing import Any, cast

import jira

# Help out mypy, it trips over directly using jira.resources.Issue.
from jira import Issue

from .config.model import JiraConfig

log = logging.getLogger(__name__)


class JiraRunMode(IntEnum):
    """Define how a JIRA instance is supposed to be accessed."""

    READ_WRITE = 0  # Read data from JIRA and make changes
    READ_ONLY = 1  # Read data from JIRA, but don’t make changes
    DRY_RUN = 2  # Don’t connect to JIRA


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
    _jira: jira.client.JIRA | None
    run_mode: JiraRunMode
    project_statuses: dict[Issue, dict[str, str]]

    def __init__(self, jira_config: JiraConfig, run_mode: JiraRunMode = JiraRunMode.READ_WRITE):
        """
        Initialize the JIRA object.

        :param jira_config: The configuration describing the JIRA instance
        :param run_mode: How JIRA is accessed
        """
        self.jira_config = jira_config

        self.run_mode = run_mode
        if run_mode == JiraRunMode.DRY_RUN:
            self._jira = None
        else:
            self._jira = jira.client.JIRA(
                str(jira_config.instance_url), token_auth=jira_config.token
            )
            # Establish that the connection is authenticated, will throw an exception without.
            self._jira.session()

        self.project_statuses = {}

    @property
    def jira(self) -> jira.client.JIRA:
        if not self._jira:
            raise RuntimeError("JIRA client object not established")
        return self._jira

    def get_issues_by_labels(
        self,
        labels: str | Collection[str] = [],
        issues_url: Collection[str] = [],
        closed: bool = False,
        filters: list[str] | None = None,
    ) -> list[Issue]:
        """
        Retrieve issues for the specified labels.

        :param labels: Labels to retrieve the issues by
        :param issues_url: URLs to match in external url field
        :param closed: Whether to return closed issues

        :return: List of issues
        """
        if isinstance(labels, str):
            labels = [labels]

        if self.run_mode == JiraRunMode.DRY_RUN:
            log.info("Skipping getting JIRA issues by labels: %s", ", ".join(labels))
            return []

        search_filters: list[str] = [f'project = "{self.jira_config.project}"']

        if labels:
            labels_str = ", ".join(f'"{label}"' for label in labels)
            search_filters.append(f"labels IN ({labels_str})")

        if closed:
            search_filters.append('status IN ("Done", "Closed")')
        else:
            search_filters.append('status NOT IN ("Done", "Closed")')

        if issues_url:
            urls = ", ".join(f'"{url}"' for url in issues_url)
            # Searching summary will prevent creating the issue again in case
            # sync from upstream is down
            search_filters.append(f'("External Issue URL" IN ({urls}) OR "Summary" IN ({urls}))')

        if filters:
            search_filters.extend(filters)

        issues = cast(
            jira.client.ResultList[Issue],
            self.jira.search_issues(
                " AND ".join(search_filters),
                maxResults=0,
                use_post=True,
            ),
        )
        return issues

    def create_issue(
        self,
        *,
        url: str,
        labels: Collection[str] | str | None = None,
    ) -> Issue | None:
        """
        Create a new issue in JIRA.

        :param url: URL to add to url field, it is also added to description
        :param labels: Label(s) for the issue, if any

        :return: Issue object or None
        """
        if self.run_mode != JiraRunMode.READ_WRITE:
            log.info("Skipping creating JIRA issue for URL %s", url)
            return None

        if isinstance(labels, str):
            labels = (labels,)

        issue_dict: dict[str, Any] = {
            "project": {"key": self.jira_config.project},
            "summary": url,
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
        if self.run_mode == JiraRunMode.DRY_RUN:
            log.info("Skipping getting JIRA issue %s transition statuses", issue.key)
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
        if self.run_mode != JiraRunMode.READ_WRITE:
            log.info("Skipping transitioning JIRA issue %s to '%s'", issue.key, status)
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
        if self.run_mode != JiraRunMode.READ_WRITE:
            log.info("Skipping assigning user '%s' to JIRA issue %s", user, issue.key)
            return

        if user != getattr(issue.fields.assignee, "name", None):
            log.debug("Assigning user %s to %s", user, issue.key)
            self.jira.assign_issue(issue.id, user)

    def add_labels(self, issue: Issue, labels: Collection[str] | str, changes: dict) -> dict:
        """
        Add label to an issue.

        :param issue: Issue object
        :param labels: Label(s) to add
        :param changes: Dictionary containing all the changes for the issue

        :return: Updated dictionary of changes
        """
        if isinstance(labels, str):
            labels = (labels,)

        labels_to_add = [label for label in labels if label not in issue.fields.labels]

        if not labels_to_add:
            log.debug("%s: Not adding any labels", issue.key)
            return changes

        log.debug("%s: Adding labels: %s", issue.key, ", ".join(labels_to_add))
        labels_field_ops = [{"add": label} for label in labels_to_add]
        return changes | {"labels": labels_field_ops}

    def add_story_points(self, issue: Issue, story_points: int, changes: dict) -> dict:
        """
        Add story points to an issue.

        :param issue: Issue object
        :param story_points: Amount of story points to add
        :param changes: Dictionary containing all the changes for the issue

        :return: Updated dictionary of changes
        """
        if not self.jira_config.story_points_field:
            log.debug("Story point field in jira is not set. Skipping adding story points.")
            return changes

        if getattr(issue.fields, self.jira_config.story_points_field) == story_points:
            log.debug("%s: story points already set to correct value. Skipping.", issue.key)
            return changes

        if story_points == 0:
            log.debug("%s: story points are set to 0. Skipping.", issue.key)
            return changes

        log.debug("%s: Adding story points: %d", issue.key, story_points)
        return changes | {self.jira_config.story_points_field: [{"set": story_points}]}

    def add_blocked_status(self, issue: Issue, blocked: bool, changes: dict) -> dict:
        """
        Add blocked state to issue.

        :param issue: Issue object
        :param blocked: True/False corresponding to blocked state
        :param changes: Dictionary containing all the changes for the issue

        :return: Updated dictionary of changes
        """
        # Convert boolean to values used by JIRA
        blocked = dict(self.jira_config.blocked_values)[str(blocked).lower()]
        if not self.jira_config.blocked_field:
            log.debug("Blocked field in jira is not set. Skipping adding blocked status.")
            return changes

        value = getattr(issue.fields, self.jira_config.blocked_field)
        if value.id == str(blocked):
            log.debug("%s: blocked field already set to correct value. Skipping.", issue.key)
            return changes

        log.debug("%s: Filling blocked field: %s", issue.key, {"id": str(blocked)})
        return changes | {self.jira_config.blocked_field: [{"set": {"id": str(blocked)}}]}

    def update_issue(self, issue: Issue, changes: dict) -> None:
        """
        Update fields on the issue.

        :param issue: Issue object
        :param changes: Dictionary containing all the changes for the issue
        """
        if self.run_mode != JiraRunMode.READ_WRITE:
            log.info("%s: Skipping updating JIRA issue with changes %s", issue.key, changes)
            return

        if not changes:
            log.info("%s: Nothing to update. Skipping.", issue.key)
            return

        log.info("%s: Updating ticket with '%s'", issue.key, changes)

        issue.update(update=changes)
