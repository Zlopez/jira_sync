"""
Script for synchronizing tickets from various trackers in JIRA project.
"""

import click
import jira
import tomllib

from jira_sync.pagure import Pagure

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

    pagure_enabled = config_dict["Pagure"]["enabled"]

    # Pagure is enabled in configuration
    if pagure_enabled:
        pagure_issues = []
        pagure = Pagure(config_dict["Pagure"]["pagure_url"])

        # Retrieve all open issues on the project
        for repository in config_dict["Pagure"]["repositories"]:
            pagure_issues.extend(
                pagure.get_project_issues(repository["repo"])
            )

        click.echo(len(pagure_issues))


if __name__ == "__main__":
    cli.add_command(sync_tickets)
    cli()
