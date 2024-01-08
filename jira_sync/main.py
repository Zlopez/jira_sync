"""
Script for synchronizing tickets from various trackers in JIRA project.
"""
import logging
import re

import click
import tomllib

from jira_sync.pagure import Pagure
from jira_sync.jira_wrapper import JIRA


@click.group()
def cli():
    pass


@click.command()
@click.option(
    "--config",
    default="config.toml",
    help="Path to configuration file."
)
@click.option('--verbose', is_flag=True, help="Will print verbose messages.")
def sync_tickets(config: str, verbose: bool):
    """
    Sync the ticket from sources provided in configuration file.

    Params:
      config: Path to configuration file.
      verbose: Verbose flag
    """
    if verbose:
        logging.basicConfig(level=logging.DEBUG)
    with open(config, "rb") as config_file:
        config_dict = tomllib.load(config_file)

    jira = JIRA(
        config_dict["General"]["jira_instance"],
        config_dict["General"]["jira_token"],
        config_dict["General"]["jira_project"],
        config_dict["General"]["jira_default_issue_type"],
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
            log.info("Processing repository: {}".format(repository["repo"]))
            repo_issues = pagure.get_open_project_issues(
                repository["repo"], repository["label"]
            )
            jira_issues = jira.get_open_issues_by_label(repository["repo"])
            log.info(
                "Retrieved {} issues from jira for '{}' repository".format(
                    len(jira_issues), repository["repo"]
                )
            )
            # This will be filled with JIRA issues that were matched with
            # pagure obtained issues
            jira_issues_matched = []
            # Add project_name to use it later as JIRA label
            for issue in repo_issues:
                # Look if the issue exists in JIRA already
                for jira_issue in jira_issues:
                    if re.match(
                            "^" + issue["full_url"] + "$",
                            jira_issue.fields.description.split("\n")[0]
                    ):
                        # We found the issue, let's remember it
                        issue["jira_issue"] = jira_issue
                        jira_issues_matched.append(jira_issue)

                # Let's filter the issues that should be closed
                issue["project"] = repository["repo"]
                if issue["assignee"]:
                    issue["ticket_state"] = "assigned"
                if "blocked" in issue["tags"]:
                    issue["ticket_state"] = "blocked"
                if issue["closed_at"]:
                    issue["ticket_state"] = "closed"
            pagure_issues.extend(
                repo_issues
            )
            log.info(
                "{} pagure issues matched jira issues".format(
                    len(jira_issues_matched)
                )
            )
            jira_issues_to_close.extend(
                [
                    jira_issue for jira_issue in jira_issues
                    if jira_issue not in jira_issues_matched
                ]
            )

        for issue in pagure_issues:
            log.debug("Processing issue: {}".format(issue["full_url"]))
            jira_issue = None
            if "jira_issue" in issue:
                jira_issue = issue["jira_issue"]
            else:
                # Find the corresponding issue in JIRA
                jira_issue = jira.get_issue_by_link(
                    issue["full_url"],
                    issue["project"],
                    issue["title"]
                )

            if not jira_issue:
                log.debug(
                    "Creating jira ticket from '{}'".format(issue["full_url"])
                )
                jira_issue = jira.create_issue(
                    issue["title"],
                    issue["content"],
                    issue["full_url"],
                    issue["project"],
                )

            if (
                    issue["assignee"] and
                    issue["assignee"]["name"] in pagure_usernames
            ):
                jira.assign_to_issue(
                    jira_issue,
                    pagure_usernames[issue["assignee"]["name"]]
                )
            else:
                jira.assign_to_issue(jira_issue, None)
            # Don't move the issue if the ticket_state value is not filled
            # This will prevent to move ticket back
            # to new state when not needed
            if "ticket_state" in issue:
                jira.transition_issue(jira_issue, state_map[issue["ticket_state"]])
            jira.add_label(jira_issue, config_dict["General"]["jira_label"])

        # Close the JIRA issues that are not open anymore on source
        log.info("Closing '{}' JIRA issues".format(len(jira_issues_to_close)))
        for jira_issue in jira_issues_to_close:
            jira.transition_issue(jira_issue, state_map["closed"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)

    cli.add_command(sync_tickets)
    cli()
