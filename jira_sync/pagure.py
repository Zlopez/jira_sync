"""
Module for communicating with pagure API.

See https://pagure.io/api/0
"""
import logging
from typing import List

import requests

log = logging.getLogger()


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

    def get_project_issues(
            self, repo: str, params: dict = {}
    ) -> List:
        """
        Retrieve all the project issues on project.

        Params:
          repo: Repository path. For example 'namespace/repo'
          params: Additional params for request
                  See https://pagure.io/api/0/#issues-tab

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
