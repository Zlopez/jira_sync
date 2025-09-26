# SPDX-FileCopyrightText: Contributors to the Fedora Project
#
# SPDX-License-Identifier: MIT

import logging

from bugzilla2fedmsg_schema.schema import MessageV1BZ4
from fedora_messaging.config import conf as fm_config

from .config import load_configuration
from .jira_wrapper import JIRA, JiraRunMode

log = logging.getLogger(__name__)


class Consumer:
    def __init__(self):
        config_file = fm_config["consumer_config"]["config_file"]
        self._config = load_configuration(config_file)
        run_mode_name = fm_config["consumer_config"].get("run_mode", "READ_WRITE")
        self._jira_config = self._config.general.jira
        self._jira_statuses = self._jira_config.statuses
        self._jira_status_values = list(self._jira_statuses.model_dump().values())
        self._jira = JIRA(self._jira_config, run_mode=JiraRunMode[run_mode_name])

    def __call__(self, message: MessageV1BZ4):
        log.debug("Consuming message %s", message.id)
        try:
            self._handle(message)
        except Exception:
            log.exception(f"Handling of {message.id} failed!")

    def _handle(self, message: MessageV1BZ4):
        if not self._bug_to_act_on(message):
            log.info("Not a BZ event we're interested in")
            return

        if existing_issues := self._look_for_jira_issue(message.bug["weburl"]):
            log.info(
                "BZ #%s has already been mirrored to %s",
                message.bug["id"],
                ", ".join(existing_issues),
            )
            return

        log.info("Creating JIRA ticket from Bugzilla ticket %s", message.bug["id"])
        jira_issue = self._jira.create_issue(
            url=message.bug["weburl"],
            labels=[
                self._jira_config.label,
                # f"bugzilla:{message.component_name}",
                "bugzilla",
                message.component_name,
            ],
        )
        if not jira_issue:
            log.error(
                "Couldn’t create new JIRA issue from Bugzilla ticket %s",
                message.bug["id"],
            )

    def _bug_to_act_on(self, message):
        mirror_flag_added = {
            "added": "+",
            "field": "flag.mirror",
            "field_name": "flag.mirror",
            "removed": "",
        }
        return (
            message.body["event"]["action"] == "modify"
            and mirror_flag_added in message.body["event"]["changes"]
        )
        # flags = {flag["name"]: flag["value"] for flag in bug["flags"]}

    def _look_for_jira_issue(self, bug_url):
        log.debug("Looking for an existing JIRA issue")
        existing_issues = self._jira.get_issues_by_labels(filters=[f'"BZ URL" = "{bug_url}"'])
        # Issues just created, before automation could run
        existing_issues.extend(self._jira.get_issues_by_labels(filters=[f'summary ~ "{bug_url}"']))
        return [issue.key for issue in existing_issues]
