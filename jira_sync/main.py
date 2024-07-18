"""
Script for synchronizing tickets from various trackers in JIRA project.
"""

import logging
from dataclasses import replace

import click

from .config import load_configuration
from .jira_wrapper import JIRA
from .repositories import IssueStatus, Repository

log = logging.getLogger(__name__)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Will print verbose messages.")
def cli(verbose: bool):
    """
    Click main function.

    :param verbose: Log verbosely, or not
    """
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    log.addHandler(ch)
    if verbose:
        log.setLevel(logging.DEBUG)
    else:
        log.setLevel(logging.INFO)


@cli.command()
@click.option(
    "--config-file", "--config", default="config.toml", help="Path to configuration file."
)
def sync_tickets(config_file: str):
    """
    Sync the ticket from sources provided in configuration file.

    :param config: Path to configuration file
    """
    config = load_configuration(config_file)
    jira_config = config.general.jira

    jira = JIRA(
        url=str(jira_config.instance_url),
        token=jira_config.token,
        project=jira_config.project,
        issue_type=jira_config.default_issue_type,
    )

    statuses = jira_config.statuses

    all_jira_issues = set()
    # This will be filled with JIRA issues that were matched with
    # issues found in repositories
    jira_issues_matched = set()
    # JIRA issues which shouldn’t be touched
    jira_issues_dubious = set()
    # All issues encountered in repositories
    all_repo_issues = set()

    for instance_spec in config.instances.values():
        for repo_name, repo_spec in instance_spec.repositories.items():
            log.info("Processing repository: %s", repo_name)
            repo = Repository.from_config(
                config=instance_spec.model_dump() | repo_spec.model_dump() | {"repo": repo_name}
            )
            if not repo.enabled:
                continue

            jira_issues = jira.get_open_issues_by_label(repo_name)
            all_jira_issues |= set(jira_issues)
            log.info(
                "Retrieved %s issues from jira for '%s' repository: %s",
                len(jira_issues),
                repo_name,
                ", ".join(issue.key for issue in jira_issues),
            )

            repo_issues = repo.get_open_issues()

            for jira_candidate in jira_issues:
                if not jira_candidate.fields.description:
                    log.warning(
                        "Skipping JIRA issue with empty description: %s", jira_candidate.key
                    )
                    jira_issues_dubious.add(jira_candidate)
                    continue

                split_desc = jira_candidate.fields.description.split("\n", 1)
                jira_full_url = split_desc[0]

                replaced_issues = []

                for idx, repo_issue in enumerate(repo_issues):
                    if (
                        jira_candidate.fields.description
                        and jira_full_url
                        and repo_issue.full_url == jira_full_url
                    ):
                        repo_issue = replace(repo_issue, jira_issue=jira_candidate)
                        replaced_issues.append((idx, repo_issue))
                        jira_issues_matched.add(jira_candidate)

                for idx, repo_issue in replaced_issues:
                    repo_issues[idx] = repo_issue

            all_repo_issues |= set(repo_issues)

            log.info("%s pagure issues matched jira issues", len(jira_issues_matched))

    jira_issues_to_close = all_jira_issues - jira_issues_matched - jira_issues_dubious

    for repo_issue in all_repo_issues:
        log.debug("Processing repo issue: %s", repo_issue.full_url)
        repo = repo_issue.repository

        jira_issue = repo_issue.jira_issue

        if not jira_issue:
            jira_issue = jira.get_issue_by_link(
                repo_issue.full_url, repo_issue.repository.repo, repo_issue.title
            )

        if not jira_issue:
            log.debug("Creating jira ticket from '%s'", repo_issue.full_url)
            jira_issue = jira.create_issue(
                repo_issue.title,
                repo_issue.content,
                repo_issue.full_url,
                repo_issue.repository.repo,
            )
            if not jira_issue:
                log.error("Couldn’t create new JIRA issue from '%s'", repo_issue.full_url)
                continue
        else:
            log.debug("Repo issue %s matched with %s", repo_issue.full_url, jira_issue.key)

        jira.assign_to_issue(
            jira_issue, repo.usermap.get(repo_issue.assignee) if repo_issue.assignee else None
        )

        jira_status = getattr(jira_config.statuses, repo_issue.status.name)
        if jira_issue.fields.status.name == jira_status:
            log.debug("Not transitioning issue %s with status %s", jira_issue.key, jira_status)
            continue

        if repo_issue.status != IssueStatus.closed:
            # Only move to new state from status we know
            if not (
                repo_issue.status == IssueStatus.new
                and jira_issue.fields.status.name not in statuses
            ):
                log.debug(
                    "Transition issue %s from %s to %s",
                    jira_issue.key,
                    jira_issue.fields.status.name,
                    jira_status,
                )
                jira.transition_issue(jira_issue, jira_status)
                jira.add_label(jira_issue, jira_config.label)
            else:
                log.debug(
                    "Not transitioning issue %s from %s to %s",
                    jira_issue.key,
                    jira_issue.fields.status.name,
                    jira_status,
                )
        else:
            log.debug("Marking issue %s for closing", jira_issue.key)
            jira_issues_to_close.add(jira_issue)

    if jira_issues_to_close:
        # Close the JIRA issues that are not open anymore on source
        log.info(
            "Closing %s JIRA issues: %s",
            len(jira_issues_to_close),
            ", ".join(issue.key for issue in jira_issues_to_close),
        )
        for jira_issue in jira_issues_to_close:
            jira.transition_issue(jira_issue, statuses.closed)
