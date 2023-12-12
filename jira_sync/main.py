"""
Script for synchronizing tickets from various trackers in JIRA project.
"""

import click
import tomllib

from jira_sync.pagure import Pagure
from jira_sync.jira_wrapper import JIRA

@click.group()
def cli():
    pass

@click.command()
@click.option("--since", help=("How many days ago to look for closed issues."
                               "Expects date in DD.MM.YYYY format (31.12.2021)."))
@click.option("--config", default="config.toml", help="Path to configuration file.")
def sync_tickets(since: str, config: str):
    """
    Sync the ticket from sources provided in configuration file.

    Params:
      since: How many days ago to look for closed issues.
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

        # Retrieve all open issues on the project
        for repository in config_dict["Pagure"]["repositories"]:
            repo_issues = pagure.get_project_issues(repository["repo"])
            # Add project_name to use it later as JIRA label
            for issue in repo_issues:
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

        issue = pagure_issues[0]

        # The method returns list, but there should be only one issue
        # per pagure ticket
        jira_issues = jira.get_issue_by_link(issue["full_url"])

        # There is something wrong if we find more than one issue
        # for the ticket
        if len(jira_issues) > 1:
            click.echo(
                "We found more than one issue for url '{}'".format(
                    issue["full_url"]
                ),
                err=True)
            click.echo(
                "JIRA issues list for the url '{}': {}".format(
                    issue["full_url"],
                    [issue.id for issue in jira_issues]
                ),
                err=True)

        if not jira_issues:
            jira_issue = jira.create_issue(
                issue["title"],
                issue["content"],
                issue["full_url"],
                issue["project"],
            )
        else:
            jira_issue = jira_issues[0]

        if issue["assignee"]:
            jira.assign_to_issue(
                jira_issue,
                pagure_usernames[issue["assignee"]["username"]]
            )
        jira.transition_issue(jira_issue, state_map[issue["ticket_state"]])


if __name__ == "__main__":
    cli.add_command(sync_tickets)
    cli()
