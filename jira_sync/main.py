"""
Script for synchronizing tickets from various trackers in JIRA project.
"""

import logging

import click
from jira.exceptions import JIRAError

from .config import load_configuration
from .jira_wrapper import JiraRunMode
from .sync_mgr import SyncManager

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
    try:
        sync_mgr = SyncManager(config=config, run_mode=run_mode)
    except JIRAError as e:
        raise click.ClickException(e.text) from e
    sync_mgr.sync_issues()
