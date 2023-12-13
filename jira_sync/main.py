"""
Script for synchronizing tickets from various trackers in JIRA project.
"""
import logging

import click
import tomllib

from jira_sync.pagure import Pagure
from jira_sync.jira_wrapper import JIRA

DEBUG = True


@click.group()
def cli():
    pass


@click.command()
@click.option(
    "--days-ago",
    default=1,
    help="How many days ago to look for closed issues."
)
@click.option(
    "--config",
    default="config.toml",
    help="Path to configuration file."
)
def sync_tickets(days_ago: int, config: str):
    """
    Sync the ticket from sources provided in configuration file.

    Params:
      days_ago: How many days ago to look for closed issues.
      config: Path to configuration file.
    """
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

    # Pagure is enabled in configuration
    if pagure_enabled:
        pagure_usernames = config_dict["Pagure"]["usernames"]
        pagure_issues = []
        pagure = Pagure(config_dict["Pagure"]["pagure_url"])

        # Retrieve issues on the project
        for repository in config_dict["Pagure"]["repositories"]:
            repo_issues = pagure.get_open_project_issues(repository["repo"])
            repo_issues.extend(
                pagure.get_closed_project_issues(repository["repo"], days_ago)
            )
            # Add project_name to use it later as JIRA label
            for issue in repo_issues:
                log.debug("Processing issue: {}".format(issue["full_url"]))
                issue["project"] = repository["repo"]
                if not issue["assignee"]:
                    issue["ticket_state"] = "new"
                else:
                    issue["ticket_state"] = "assigned"
                if "blocked" in issue["tags"]:
                    issue["ticket_state"] = "blocked"
                if issue["closed_at"]:
                    issue["ticket_state"] = "closed"
            pagure_issues.extend(
                repo_issues
            )

        for issue in pagure_issues:
            # The method returns list, but there should be only one issue
            # per pagure ticket
            jira_issues = jira.get_issue_by_link(
                issue["full_url"],
                issue["project"],
                issue["title"]
            )

            # There is something wrong if we find more than one issue
            # for the ticket
            if len(jira_issues) > 1:
                log.error(
                    "We found more than one issue for url '{}': {}".format(
                        issue["full_url"],
                        [issue.key for issue in jira_issues]
                    ))

            if not jira_issues:
                log.debug(
                    "Creating jira ticket from '{}'".format(issue["full_url"])
                )
                jira_issue = jira.create_issue(
                    issue["title"],
                    issue["content"],
                    issue["full_url"],
                    issue["project"],
                )
            else:
                jira_issue = jira_issues[0]

            if (
                    issue["assignee"] and
                    issue["assignee"]["name"] in pagure_usernames
            ):
                jira.assign_to_issue(
                    jira_issue,
                    pagure_usernames[issue["assignee"]["name"]]
                )
            jira.transition_issue(jira_issue, state_map[issue["ticket_state"]])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)
    if DEBUG:
        log.setLevel(logging.DEBUG)

    cli.add_command(sync_tickets)
    cli()
