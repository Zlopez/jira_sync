#!/usr/bin/env python3
"""
Script for mirroring Bugzilla tickets with the 'mirror' flag set to '+' into JIRA.

This script queries a Bugzilla instance for tickets that have the 'mirror' flag
set to '+' and creates corresponding issues in JIRA.
"""

import logging
import sys
from pathlib import Path

import bugzilla
import click

sys.path.insert(0, str(Path(__file__).parent.parent))

from jira_sync.config import load_configuration
from jira_sync.jira_wrapper import JIRA, JiraRunMode

log = logging.getLogger(__name__)


@click.command()
@click.argument("bugzilla_url")
@click.option(
    "--config-file",
    "--config",
    default="config.toml",
    help="Path to configuration file.",
    type=click.Path(exists=True, path_type=Path),
)
@click.option(
    "--dry-run",
    "-n",
    is_flag=True,
    help="Don't create JIRA issues, just show what would be created.",
)
@click.option("-v", "--verbose", is_flag=True, help="Will print verbose messages.")
@click.option(
    "--all-states",
    is_flag=True,
    help="Process bugs in all states (default: only NEW and ASSIGNED).",
)
def main(bugzilla_url: str, config_file: Path, dry_run: bool, verbose: bool, all_states: bool):
    """
    Mirror Bugzilla tickets with 'mirror' flag set to '+' into JIRA.

    BUGZILLA_URL: The URL of the Bugzilla instance (e.g., https://bugzilla.redhat.com)
    """
    if verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        format="%(levelname)s: %(message)s",
        level=level,
    )

    log.info("Connecting to Bugzilla at %s", bugzilla_url)
    bzapi = bugzilla.Bugzilla(bugzilla_url)

    log.info("Loading configuration from %s", config_file)
    config = load_configuration(config_file)

    jira_config = config.general.jira
    run_mode = JiraRunMode.READ_ONLY if dry_run else JiraRunMode.READ_WRITE

    log.info("Initializing JIRA client (mode: %s)", run_mode.name)
    jira_client = JIRA(jira_config, run_mode=run_mode)

    if all_states:
        log.info("Querying Bugzilla for tickets with 'mirror' flag set to '+' (all states)")
        query = bzapi.build_query(flag="mirror+")
    else:
        log.info(
            "Querying Bugzilla for tickets with 'mirror' flag set to '+' (NEW and ASSIGNED only)"
        )
        query = bzapi.build_query(flag="mirror+", status=["NEW", "ASSIGNED"])

    # Use pagination to handle large result sets
    # Setting limit=0 requests the server-side limit (usually 1000)
    offset = 0
    limit = 20
    all_bugs = []

    while True:
        query["limit"] = limit
        query["offset"] = offset

        log.debug("Fetching bugs with offset=%d, limit=%d", offset, limit)
        bugs = bzapi.query(query)

        if not bugs:
            break

        all_bugs.extend(bugs)
        log.info("Fetched %d bugs (total so far: %d)", len(bugs), len(all_bugs))

        # If we got fewer bugs than the limit, we've reached the end
        if len(bugs) < limit:
            break

        offset += limit

    log.info("Found %d total bugs with mirror flag", len(all_bugs))

    for bug in all_bugs:
        bug_url = bug.weburl
        bug_id = bug.id

        log.info("Processing BZ #%s: %s", bug_id, bug_url)

        existing_issues = _look_for_jira_issue(jira_client, bug_url)
        if existing_issues:
            log.info(
                "BZ #%s has already been mirrored to %s",
                bug_id,
                ", ".join(existing_issues),
            )
            continue

        component = bug.component
        labels = [
            jira_config.label,
            "bugzilla",
            component,
        ]

        log.info("Creating JIRA ticket from Bugzilla ticket %s", bug_id)
        jira_issue = jira_client.create_issue(
            url=bug_url,
            labels=labels,
        )

        if jira_issue:
            log.info("Created JIRA issue: %s", jira_issue.key)
        elif not dry_run:
            log.error(
                "Couldn't create new JIRA issue from Bugzilla ticket %s",
                bug_id,
            )


def _look_for_jira_issue(jira_client: JIRA, bug_url: str) -> list[str]:
    """
    Check if a JIRA issue already exists for the given Bugzilla URL.

    :param jira_client: JIRA client instance
    :param bug_url: Bugzilla ticket URL

    :return: List of existing JIRA issue keys
    """
    log.debug("Looking for an existing JIRA issue for %s", bug_url)
    existing_issues = jira_client.get_issues_by_labels(
        filters=[f'"BZ URL" = "{bug_url}"']
    )
    # Issues just created, before automation could run
    existing_issues.extend(
        jira_client.get_issues_by_labels(filters=[f'summary ~ "{bug_url}"'])
    )
    return [issue.key for issue in existing_issues]


if __name__ == "__main__":
    main()
