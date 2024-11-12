"""
Script for synchronizing tickets from various trackers in JIRA project.
"""

import logging
from dataclasses import replace

import click

from .config import load_configuration
from .jira_wrapper import JIRA, JiraRunMode
from .repositories import Instance, IssueStatus

log = logging.getLogger(__name__)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Will print verbose messages.")
def cli(verbose: bool):
    """
    Click main function.

    :param verbose: Log verbosely, or not
    """
    if verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        format="%(message)s",
        level=level,
    )


@cli.command()
@click.option(
    "--config-file", "--config", default="config.toml", help="Path to configuration file."
)
@click.option(
    "--read-write",
    "-w",
    "run_mode",
    type=JiraRunMode,
    flag_value=JiraRunMode.READ_WRITE,
    default=True,
    help="Read data from JIRA and make changes.",
)
@click.option(
    "--read-only",
    "-r",
    "run_mode",
    type=JiraRunMode,
    flag_value=JiraRunMode.READ_ONLY,
    help="Read data from JIRA but don’t attempt to make changes.",
)
@click.option(
    "--dry-run",
    "-n",
    "run_mode",
    type=JiraRunMode,
    flag_value=JiraRunMode.DRY_RUN,
    help="Don’t change anything.",
)
def sync_tickets(config_file: str, run_mode: JiraRunMode):
    """
    Sync the ticket from sources provided in configuration file.

    :param config: Path to configuration file
    :param run_mode: How JIRA is supposed to be accessed
    """
    config = load_configuration(config_file)
    jira_config = config.general.jira

    jira = JIRA(jira_config, run_mode=run_mode)

    statuses = jira_config.statuses
    status_values = list(statuses.model_dump().values())

    all_jira_issues = set()
    # This will be filled with JIRA issues that were matched with
    # issues found in repositories
    jira_issues_matched = set()
    # JIRA issues which shouldn’t be touched
    jira_issues_dubious = set()
    # All issues encountered in repositories
    all_repo_issues = set()

    instances_by_name = {
        instance_name: Instance.from_config(
            name=instance_name, config_path=config.config_path, config=instance_spec
        )
        for instance_name, instance_spec in config.instances.items()
        if instance_spec.enabled
    }

    for instance_name, instance in instances_by_name.items():
        log.info("Processing instance: %s", instance_name)
        for repo_name, repo in instance.repositories.items():
            log.info("Processing repository: %s:%s", instance_name, repo_name)
            if not repo.enabled:
                continue

            jira_issues = jira.get_issues_by_labels((f"{instance_name}:{repo_name}", repo_name))
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

            log.info("%s %s issues matched jira issues", len(jira_issues_matched), repo.name)

    jira_issues_to_close = all_jira_issues - jira_issues_matched - jira_issues_dubious

    for repo_issue in all_repo_issues:
        log.debug("Processing repo issue: %s", repo_issue.full_url)
        repo = repo_issue.repository

        jira_issue = repo_issue.jira_issue

        if not jira_issue:
            jira_issue = jira.get_issue_by_link(
                url=repo_issue.full_url,
                instance=repo_issue.repository.instance.name,
                repo=repo_issue.repository.name,
            )

        if not jira_issue:
            log.debug("Creating jira ticket from '%s'", repo_issue.full_url)
            instance_name = repo_issue.repository.instance.name
            repo_name = repo_issue.repository.name
            jira_issue = jira.create_issue(
                summary=repo_issue.title,
                description=repo_issue.content,
                url=repo_issue.full_url,
                labels=[jira_config.label, f"{instance_name}:{repo_name}"],
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
                and jira_issue.fields.status.name not in status_values
            ):
                log.info(
                    "Transition issue %s from %s to %s",
                    jira_issue.key,
                    jira_issue.fields.status.name,
                    jira_status,
                )
                jira.transition_issue(jira_issue, jira_status)
                instance_name = repo_issue.repository.instance.name
                repo_name = repo_issue.repository.name
                jira.add_labels(jira_issue, [jira_config.label, f"{instance_name}:{repo_name}"])
            else:
                log.info(
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
