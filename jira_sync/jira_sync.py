"""
Script for synchronizing tickets from various trackers in JIRA project.
"""

import click
import jira
import tomllib


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
    global CONFIG

    with open(config, "rb") as config_file:
        CONFIG = tomllib.load(config_file)

if __name__ == "__main__":
    cli.add_command(sync_tickets)
    cli()
