"""
Module for communicating with pagure API.

See https://pagure.io/api/0
"""
import logging
from typing import List

import arrow
import requests

log = logging.getLogger(__name__)


class Pagure:
    """Wrapper class around pagure API calls."""

    instance_url: str = ""

    def __init__(self, url: str):
        """
        Class constructor.

        Params:
          url: Pagure server URL
        """
        # Remove trailing /
        if url.endswith("/"):
            url = url[:-1]
        self.instance_url = url

    def get_closed_project_issues(self, repo: str, days_ago: int) -> List:
        """
        Retrieve closed issues on project that were closed in `days_ago`.

        Params:
          repo: Repository path. For example 'namespace/repo'
          days_ago: Number of days to look in past for closed issues

        Returns:
          List of issues represented by dictionaries.
        """
        since_arg = arrow.utcnow().shift(days=-days_ago)
        next_page = (
            self.instance_url + "/api/0/" + repo +
            "/issues?status=Closed&since=" + str(since_arg.int_timestamp)
        )

        issues = []

        while next_page:
            page_data = self._get_json(next_page)
            if page_data:
                issues.extend(page_data["issues"])
                next_page = page_data["pagination"]["next"]

        log.info(
            "Retrieved {} closed issues from {}".format(
                len(issues),
                repo
            )
        )

        return issues

    def get_open_project_issues(self, repo: str) -> List:
        """
        Retrieve all open project issues on project.

        Params:
          repo: Repository path. For example 'namespace/repo'

        Returns:
          List of issues represented by dictionaries.
        """
        next_page = self.instance_url + "/api/0/" + repo + "/issues"

        issues = []

        while next_page:
            page_data = self._get_json(next_page)
            if page_data:
                issues.extend(page_data["issues"])
                next_page = page_data["pagination"]["next"]

        log.info(
            "Retrieved {} open issues from {}".format(
                len(issues),
                repo
            )
        )

        return issues

    def _get_json(self, url: str) -> dict:
        """
        Get page data from url.

        Params:
          url: URL to retrieve

        Returns:
          Dictionary representing the JSON data returned for requested url.
        """
        request = requests.get(url)

        if request.status_code == requests.codes.ok:
            return request.json()
        else:
            log.error(
                "Error happened during retrieval of '%s'. Error_code: %i",
                url,
                request.status_code
            )

        return {}
