"""
Manage synchronization between JIRA and upsteam git forges.
"""

import logging
from collections.abc import Collection
from functools import cached_property
from typing import TYPE_CHECKING, Any

from jira import Issue as JiraIssue

from .config import Config
from .jira_wrapper import JIRA, JiraRunMode
from .repositories import Instance, Issue, IssueStatus

if TYPE_CHECKING:
    from .config.model import Config, JiraConfig

log = logging.getLogger(__name__)

MatchedIssue = tuple[JiraIssue, Issue]


class SyncManager:
    """This class synchronizes tickets between git forges and JIRA."""

    _config: "Config"
    _jira_config: "JiraConfig"
    _jira: JIRA
    _instances_by_name: dict[str, Instance]

    def __init__(self, config: Config, run_mode: JiraRunMode) -> None:
        """Initialize a SyncManager instance.

        :param config: A configuration structure
        :param run_mode: Specify how to access JIRA (read-write, read-only
            or dry-run)
        """
        self._config = config
        self._jira_config = config.general.jira
        self._jira_statuses = self._jira_config.statuses
        self._jira_status_values = list(self._jira_statuses.model_dump().values())
        self._jira = JIRA(self._jira_config, run_mode=run_mode)

        self._instances_by_name = {
            instance_name: Instance.from_config(
                name=instance_name, config_path=config.config_path, config=instance_spec
            )
            for instance_name, instance_spec in config.instances.items()
            if instance_spec.enabled
        }

    def sync_issues(self) -> None:
        """Synchronize issues between the forges and JIRA.

        This is an umbrella method, delegating to other methods for individual
        tasks.
        """
        log.info("Synchronizing issues…")
        open_jira_issues = self.filter_open_jira_issues_by_forge_repo(
            self.retrieve_open_jira_issues()
        )
        log.info("Retrieved %d open JIRA issues", len(open_jira_issues))
        forge_issues = self.retrieve_forge_issues()

        matched_issues, unmatched_jira_issues, unmatched_forge_issues = (
            self.match_jira_forge_issues(open_jira_issues, forge_issues)
        )
        log.info(
            "=> %d matched, %d unmatched JIRA, %d unmatched forge issues",
            len(matched_issues),
            len(unmatched_jira_issues),
            len(unmatched_forge_issues),
        )

        self.close_jira_issues(unmatched_jira_issues)

        matched_issues |= self.create_or_reopen_jira_issues(unmatched_forge_issues)

        self.reconcile_jira_forge_issues(matched_issues)
        log.info("Done synchronizing issues.")

    def retrieve_open_jira_issues(self) -> Collection[JiraIssue]:
        """Retrieve open issues from JIRA.

        :return: A collection of JIRA issues
        """
        log.info("Retrieving open JIRA issues…")
        return self._jira.get_issues_by_labels(self._jira_config.label)

    def retrieve_forge_issues(self) -> Collection[Issue]:
        """Retrieve issues from configured forge instances.

        :return: A collection of forge issues
        """
        log.info("Retrieving open forge issues…")
        issues = []
        for instance in self._instances_by_name.values():
            log.info("Querying forge instance %s…", instance.name)
            for repo in instance.repositories.values():
                if not repo.enabled:
                    continue
                log.info("Querying repository %s:%s…", instance.name, repo.name)
                issues.extend(repo.get_issues())
                # Retrieve closed issues
                if instance.retrieve_closed_days_ago:
                    issues.extend(repo.get_issues(closed=True))
        return issues

    @cached_property
    def jira_repo_labels(self) -> Collection[str]:
        """Compute labels identifying synchronized JIRA issues.

        :return: A collection of label strings
        """
        return {
            f"{instance_name}:{repo_name}"
            for instance_name, instance in self._instances_by_name.items()
            for repo_name, repo in instance.repositories.items()
            if repo.enabled
        }

    def filter_open_jira_issues_by_forge_repo(
        self, jira_issues: Collection[JiraIssue]
    ) -> Collection[JiraIssue]:
        """Filters open JIRA issues for configured forge repositories.

        This is to avoid messing with JIRA issues that pertain to other than
        the configured and enabled forges or repositories.

        :param jira_issues: The JIRA issues to be filtered

        :return: A collection of JIRA issues
        """
        return [
            issue
            for issue in jira_issues
            if any(label in self.jira_repo_labels for label in issue.fields.labels)
        ]

    def get_full_url_from_jira_issue(self, jira_issue: JiraIssue) -> str | None:
        """Extract issue URL on the forge from a JIRA issue.

        :param jira_issue: The JIRA issue from which to extract the URL

        :return: The URL, or None if the JIRA description is empty
        """
        full_url = getattr(jira_issue.fields, self._jira_config.external_url_field, None)
        return full_url

    def match_jira_forge_issues(
        self, jira_issues: Collection[JiraIssue], forge_issues: Collection[Issue]
    ) -> tuple[set[MatchedIssue], set[JiraIssue], set[Issue]]:
        """Match JIRA issues with those from forges.

        :param jira_issues: The JIRA issues to be matched up
        :param forge_issues: The forge issues to be matched up

        :returns: Matched pairs of JIRA and forge issues, unmatched jira
            issues and unmatched forge issues
        """
        log.info("Matching JIRA and forge issues…")
        matched_issues = set()
        unmatched_jira_issues = set()
        unmatched_forge_issues = set(forge_issues)

        for jira_issue in jira_issues:
            full_url = self.get_full_url_from_jira_issue(jira_issue)
            match = None
            for forge_issue in unmatched_forge_issues:
                if forge_issue.full_url == full_url:
                    log.debug(
                        "%s: Matched with forge issue %s", jira_issue.key, forge_issue.full_url
                    )
                    match = (jira_issue, forge_issue)
                    matched_issues.add(match)
                    break
            if match:
                unmatched_forge_issues.remove(forge_issue)
            else:
                log.debug("%s: Unmatched with forge issue", jira_issue.key)
                unmatched_jira_issues.add(jira_issue)

        return matched_issues, unmatched_jira_issues, unmatched_forge_issues

    def close_jira_issues(self, jira_issues: Collection[JiraIssue]) -> None:
        """Close JIRA issues.

        :param jira_issues: The JIRA issues to be closed
        """
        log.info(
            "Closing %s JIRA issues: %s",
            len(jira_issues),
            ", ".join(jira_issue.key for jira_issue in jira_issues),
        )
        for jira_issue in jira_issues:
            log.info(
                "%s: Transitioning issue from %s to %s",
                jira_issue.key,
                jira_issue.fields.status.name,
                self._jira_statuses.closed,
            )
            self._jira.transition_issue(jira_issue, self._jira_statuses.closed)

    def create_or_reopen_jira_issues(self, forge_issues: Collection[Issue]) -> set[MatchedIssue]:
        """Create or reopen JIRA issues for issues on forges.

        :param forge_issues: The forge issues which should have their
            corresponding JIRA issues looked up or created.
        :return: A set of pairs of matched JIRA and forge issues
        """
        if not forge_issues:
            log.info("No JIRA issues to create or reopen.")
            return set()

        log.info("Creating/reopening JIRA issues for unmatched forge issues…")

        log.info("Retrieving list of closed JIRA issues…")
        closed_jira_issues = self._jira.get_issues_by_labels(self._jira_config.label, closed=True)
        log.info("Retrieved %d closed JIRA issues", len(closed_jira_issues))

        log.info("Matching closed JIRA issues with unmatched forge issues…")
        matched_issues, unmatched_jira_issues, unmatched_forge_issues = (
            self.match_jira_forge_issues(closed_jira_issues, forge_issues)
        )
        log.debug(
            "=> %d matched, %d unmatched JIRA, %d unmatched forge issues",
            len(matched_issues),
            len(unmatched_jira_issues),
            len(unmatched_forge_issues),
        )

        log.info("Creating JIRA issues for still unmatched forge issues…")
        for forge_issue in unmatched_forge_issues:
            log.info("Creating JIRA ticket from %s", forge_issue.full_url)
            instance_name = forge_issue.repository.instance.name
            repo_name = forge_issue.repository.name
            jira_issue = self._jira.create_issue(
                url=forge_issue.full_url,
                labels=[self._jira_config.label, f"{instance_name}:{repo_name}"],
            )
            if not jira_issue:
                log.error("Couldn’t create new JIRA issue from '%s'", forge_issue.full_url)
                continue
            matched_issues.add((jira_issue, forge_issue))

        return matched_issues

    def reconcile_jira_forge_issues(self, matched_issues: Collection[MatchedIssue]) -> None:
        """Reconcile state of JIRA issues with their forge issues.

        :param matched_issues: A collection of matched pairs of a JIRA issue
            and its forge issue
        """
        log.info("Reconciling matched JIRA and forge issues…")
        for jira_issue, forge_issue in matched_issues:
            forge_jira_assignee = (
                forge_issue.repository.usermap.get(forge_issue.assignee)
                if forge_issue.assignee
                else None
            )
            jira_assignee = jira_issue.fields.assignee
            if (
                bool(forge_jira_assignee) is not bool(jira_assignee)
                or jira_assignee
                and forge_jira_assignee
                and forge_jira_assignee not in (jira_assignee.key, jira_assignee.emailAddress)
            ):
                log.info(
                    "%s: Changing assignee from %r to %r",
                    jira_issue.key,
                    jira_assignee.key if jira_assignee else None,
                    forge_jira_assignee,
                )
                self._jira.assign_to_issue(jira_issue, forge_jira_assignee)
            else:
                if jira_assignee:
                    log.debug(
                        "%s: Not changing assignee from '%s <%s>' to %r",
                        jira_issue.key,
                        jira_assignee.key,
                        jira_assignee.emailAddress,
                        forge_jira_assignee,
                    )
                else:
                    log.debug("%s: Not assigning to %r", jira_issue.key, forge_jira_assignee)

            jira_status = getattr(self._jira_config.statuses, forge_issue.status.name)
            if jira_issue.fields.status.name == jira_status:
                log.debug("%s: Not transitioning issue with status %s", jira_issue.key, jira_status)
            else:
                # Only move to new state from status we know
                if (
                    forge_issue.status == IssueStatus.new
                    and jira_issue.fields.status.name not in self._jira_status_values
                ):
                    log.info(
                        "%s: Not transitioning status from %s to %s",
                        jira_issue.key,
                        jira_issue.fields.status.name,
                        jira_status,
                    )
                else:
                    log.info(
                        "%s: Transitioning issue from %s to %s",
                        jira_issue.key,
                        jira_issue.fields.status.name,
                        jira_status,
                    )
                    self._jira.transition_issue(jira_issue, jira_status)
            # Update the issue
            changes: dict[str, Any] = {}
            instance_name = forge_issue.repository.instance.name
            repo_name = forge_issue.repository.name
            changes = self._jira.add_labels(
                jira_issue,
                (self._jira_config.label, f"{instance_name}:{repo_name}"),
                changes,
            )
            changes = self._jira.add_story_points(jira_issue, forge_issue.story_points, changes)
            self._jira.update_issue(jira_issue, changes)
