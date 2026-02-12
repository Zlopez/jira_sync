"""
Module for communicating with pagure API.

See https://pagure.io/api/0
"""

import datetime as dt
import logging
from typing import Any

import requests

from .base import APIBase, Instance, Issue, IssueStatus, Repository

log = logging.getLogger(__name__)


class PagureBase(APIBase):
    def get_next_page(
        self,
        *,
        endpoint: str | None = None,
        response: requests.Response | None = None,
        **kwargs,
    ) -> dict[str, Any] | None:
        if response:
            api_result = response.json()
            url = api_result["pagination"]["next"]
            # Url already contains the params we used for the first call
            # There is no need to add them again
            if not url:
                return None
            return {"url": url}
        else:
            if endpoint:
                endpoint = "/" + endpoint.lstrip("/")
            else:
                endpoint = ""

            url = f"{self.get_base_url()}{endpoint}"

        return self.sanitize_requests_params(kwargs | {"url": url})


class PagureRepository(PagureBase, Repository):
    """Wrapper class around pagure API for a single repository."""

    _api_result_selectors = {"issues": "issues"}

    def get_base_url(self) -> str:
        return f"{self.instance.instance_api_url}/{self.name}"

    def normalize_issue(self, api_result: dict[str, Any]) -> Issue:
        full_url = api_result["full_url"]
        title = api_result["title"]
        content = api_result["content"]
        _assignee = api_result["assignee"]
        _status = api_result["status"].lower()
        tags = api_result["tags"]

        if _assignee:
            assignee = _assignee["name"]
        else:
            assignee = None

        if _status != "closed":
            if self.blocked_label and self.blocked_label in tags:
                status = IssueStatus.blocked
            else:
                if not _assignee:
                    status = IssueStatus.new
                else:
                    status = IssueStatus.assigned
        else:
            status = IssueStatus.closed

        story_points = 0
        priority = ""

        for tag in tags:
            if tag in self.labels_to_story_points.keys():
                story_points = max(story_points, self.labels_to_story_points[tag])
            if tag in self.labels_to_priority.keys():
                priority = self.labels_to_priority[tag]

        return Issue(
            repository=self,
            full_url=full_url,
            title=title,
            content=content,
            assignee=assignee,
            status=status,
            story_points=story_points,
            priority=priority,
        )

    def get_issue_params(self, closed: bool) -> dict[str, Any]:
        params = {}
        if closed:
            params = {
                "status": "Closed",
                "since": int(
                    (
                        dt.datetime.now(dt.UTC) - dt.timedelta(days=self.retrieve_closed_days_ago)
                    ).timestamp()
                ),
            }
        if not self.label:
            return {"params": params}
        else:
            params["tags"] = self.label
        return {"params": params}


class PagureInstance(PagureBase, Instance):
    """Wrapper class around pagure API for the whole instance."""

    type = "pagure"
    repo_cls = PagureRepository

    @property
    def instance_api_url(self) -> str | None:
        if not hasattr(self, "_instance_api_url"):
            self._instance_api_url = f"{self.instance_url}/api/0"
        return self._instance_api_url

    @instance_api_url.setter
    def instance_api_url(self, value: str) -> None:
        self._instance_api_url = value

    def query_repositories(self) -> dict[str, dict[str, Any]]:
        """Query repositores in bulk from Pagure.

        :returns: The repository names/paths and their configurations on this
            instance.
        """
        repos: dict[str, dict[str, Any]] = {}

        log.debug("Querying '%s' for repositories", self.name)

        for spec in self._query_repositories:
            log.debug("query spec: %s", spec)

            if not spec["enabled"]:
                continue

            query_params = {
                key: value
                for key, value in spec.items()
                if key in ("namespace", "pattern") and value is not None
            } | {
                "fork": False,
                "short": True,
            }

            repo_params = {
                key: value
                for key, value in spec.items()
                if key not in ("namespace", "pattern") and value is not None
            }

            response = None

            while next_page := self.get_next_page(
                endpoint="projects", response=response, params=query_params
            ):
                log.debug("next_page: %s", next_page)
                response = requests.get(**next_page)
                log.debug("response: %s", response)
                if response.status_code == requests.codes.ok:
                    api_result = response.json()
                    repos |= {proj["fullname"]: repo_params for proj in api_result["projects"]}
                else:
                    response.raise_for_status()

        # Ensure sorted iteration later
        repos = {key: repos[key] for key in sorted(repos)}

        log.info("Discovered repositories on %s: %s", self.name, ", ".join(repos))

        return repos
