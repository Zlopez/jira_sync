"""
Script for synchronizing tickets from various trackers in JIRA project.
"""

import logging
import tomllib

import click

from .jira_wrapper import JIRA
from .pagure import Pagure

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
@click.option("--config", default="config.toml", help="Path to configuration file.")
def sync_tickets(config: str):
    """
    Sync the ticket from sources provided in configuration file.

    :param config: Path to configuration file
    """
    with open(config, "rb") as config_file:
        config_dict = tomllib.load(config_file)

    jira = JIRA(
        url=config_dict["General"]["jira_instance"],
        token=config_dict["General"]["jira_token"],
        project=config_dict["General"]["jira_project"],
        issue_type=config_dict["General"]["jira_default_issue_type"],
    )

    pagure_enabled = config_dict["Pagure"]["enabled"]
    state_map = config_dict["General"]["states"]

    jira_issues_to_close = []

    # Pagure is enabled in configuration
    if pagure_enabled:
        pagure_usernames = config_dict["Pagure"]["usernames"]
        pagure_issues = []
        pagure = Pagure(config_dict["Pagure"]["pagure_url"])

        # Retrieve issues on the project
        for repository in config_dict["Pagure"]["repositories"]:
            log.info("Processing repository: %s", repository["repo"])
            repo_issues = pagure.get_open_project_issues(repository["repo"], repository["label"])
            log.info(
                "Retrieved %s issues from Pagure for '%s' repository: %s",
                len(repo_issues),
                repository["repo"],
                ", ".join(str(issue["id"]) for issue in repo_issues),
            )
            jira_issues = jira.get_open_issues_by_label(repository["repo"])
            log.info(
                "Retrieved %s issues from jira for '%s' repository: %s",
                len(jira_issues),
                repository["repo"],
                ", ".join(issue.key for issue in jira_issues),
            )
            # This will be filled with JIRA issues that were matched with
            # pagure obtained issues
            jira_issues_matched = []
            # Add project_name to use it later as JIRA label
            for issue in repo_issues:
                # Look if the issue exists in JIRA already
                for jira_candidate in jira_issues:
                    if (
                        jira_candidate.fields.description
                        and issue["full_url"] == jira_candidate.fields.description.split("\n")[0]
                    ):
                        # We found the issue, let's remember it
                        issue["jira_issue"] = jira_candidate
                        jira_issues_matched.append(jira_candidate)

                # Let's filter the issues that should be closed
                issue["project"] = repository["repo"]

                # FIXME: This is Pagure specific
                if issue["status"].lower() != "closed":
                    if not issue["assignee"]:
                        issue["ticket_state"] = "new"
                    else:
                        issue["ticket_state"] = "assigned"
                    if "blocked" in issue["tags"]:
                        issue["ticket_state"] = "blocked"
                else:
                    issue["ticket_state"] = "closed"

            pagure_issues.extend(repo_issues)
            log.info("%s pagure issues matched jira issues", len(jira_issues_matched))
            jira_issues_to_close.extend(
                [jira_issue for jira_issue in jira_issues if jira_issue not in jira_issues_matched]
            )

        for issue in pagure_issues:
            log.debug("Processing issue: %s", issue["full_url"])
            jira_issue = None
            if "jira_issue" in issue and issue["jira_issue"]:
                jira_issue = issue["jira_issue"]
                log.debug("Issue %s matched with %s", issue["full_url"], jira_issue.key)
            else:
                # Find the corresponding issue in JIRA
                jira_issue = jira.get_issue_by_link(
                    issue["full_url"], issue["project"], issue["title"]
                )

            if not jira_issue:
                log.debug("Creating jira ticket from '%s'", issue["full_url"])
                jira_issue = jira.create_issue(
                    issue["title"],
                    issue["content"],
                    issue["full_url"],
                    issue["project"],
                )

            if issue["assignee"] and issue["assignee"]["name"] in pagure_usernames:
                jira.assign_to_issue(jira_issue, pagure_usernames[issue["assignee"]["name"]])
            else:
                jira.assign_to_issue(jira_issue, None)

            if jira_issue.fields.status.name == state_map[issue["ticket_state"]]:
                log.debug(
                    "Not transitioning issue %s with state %s",
                    jira_issue.key,
                    jira_issue.fields.status.name,
                )
                continue

            if issue["ticket_state"] != "closed":
                # Don't move ticket from states we don't know
                log.debug(
                    "Transition issue %s from %s to %s",
                    jira_issue.key,
                    jira_issue.fields.status.name,
                    state_map[issue["ticket_state"]],
                )
                # Only move to new state from status we know
                if not (
                    issue["ticket_state"] == "new"
                    and jira_issue.fields.status.name not in state_map.values()
                ):
                    jira.transition_issue(jira_issue, state_map[issue["ticket_state"]])
                jira.add_label(jira_issue, config_dict["General"]["jira_label"])
            else:
                log.debug("Marking issue %s for closing", jira_issue.key)
                jira_issues_to_close.append(jira_issue)

        # Close the JIRA issues that are not open anymore on source
        log.info(
            "Closing %s JIRA issues: %s",
            len(jira_issues_to_close),
            ", ".join(issue.key for issue in jira_issues_to_close),
        )
        for jira_issue in jira_issues_to_close:
            jira.transition_issue(jira_issue, state_map["closed"])
