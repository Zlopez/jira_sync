"""
Module for communicating with the Forgejo REST API.

See https://forgejo.org/docs/latest/user/api-usage/
"""

import datetime as dt
import logging
from typing import Any, ClassVar

import requests
from requests.utils import parse_header_links

from .base import APIBase, Instance, Issue, IssueStatus, Repository

log = logging.getLogger(__name__)


class ForgejoBase(APIBase):
    API_VERSION: ClassVar[str] = "v1"

    def get_next_page(
        self,
        *,
        endpoint: str | None = None,
        response: requests.Response | None = None,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        **kwargs,
    ) -> dict[str, Any] | None:
        _params = params.copy() if params else {}

        if response:
            if "link" not in response.headers:
                return None

            # per_page would be in the pagination links in the header, drop it
            _params.pop("limit", None)

            for link in parse_header_links(response.headers["link"]):
                if link["rel"] == "next":
                    url = link["url"]
                    break
            else:
                return None
        else:
            _params.setdefault("limit", "50")
            if endpoint:
                endpoint = "/" + endpoint.lstrip("/")
            else:
                endpoint = ""

            url = f"{self.get_base_url()}{endpoint}"

        _headers = {}

        if self.token:
            _headers["AuthorizationHeaderToken"] = f"token {self.token}"

        if headers:
            _headers |= headers

        kwargs["headers"] = _headers
        kwargs["params"] = _params

        return self.sanitize_requests_params(kwargs | {"url": url})


class ForgejoRepository(ForgejoBase, Repository):
    """Wrapper class around the Forgejo REST API for a single repository."""

    def get_base_url(self) -> str:
        return f"{self.instance.get_base_url()}/repos/{self.name}"

    def normalize_issue(self, api_result: dict[str, Any]) -> Issue:
        full_url = api_result["html_url"]
        title = api_result["title"]
        content = api_result["body"]
        _assignee = api_result["assignee"]
        _state = api_result["state"].lower()
        _labels = [
            label["name"] if isinstance(label, dict) else label for label in api_result["labels"]
        ]

        if _assignee:
            assignee = _assignee["login"]
        else:
            assignee = None

        if _state != "closed":
            if self.blocked_label and self.blocked_label in _labels:
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

        for tag in _labels:
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
                "state": "closed",
                "since": (
                    dt.datetime.now(dt.UTC) - dt.timedelta(days=self.retrieve_closed_days_ago)
                ).isoformat(timespec="seconds"),  # We need to format to ISO 8601
            }
        if not self.label:
            return {"params": params}
        else:
            params["labels"] = self.label
        return {"params": params}


class ForgejoInstance(ForgejoBase, Instance):
    """Wrapper class around the Forgejo REST API for the whole instance."""

    type = "forgejo"
    repo_cls = ForgejoRepository

    def query_repositories(self) -> dict[str, dict[str, Any]]:
        """Query repositores in bulk from Forgejo.

        :returns: The repository names/paths and their configurations on this
            instance.
        """
        repos: dict[str, dict[str, Any]] = {}

        log.info("Querying '%s' for repositories", self.name)

        for spec in self._query_repositories:
            log.debug("query spec: %s", spec)

            if not spec["enabled"]:
                continue

            query_params = {key: value for key, value in spec.items() if key in ("org", "user")}

            repo_params = {
                key: value
                for key, value in spec.items()
                if key not in ("org", "user") and value is not None
            }

            match query_params:
                case {"org": org}:
                    endpoint = f"/orgs/{org}/repos"
                case {"user": user}:  # pragma: no branch
                    endpoint = f"/users/{user}/repos"

            response = None

            while next_page := self.get_next_page(endpoint=endpoint, response=response):
                response = requests.get(**next_page)
                if response.status_code == requests.codes.ok:
                    api_result = response.json()
                    repos |= {
                        repo["full_name"]: repo_params
                        for repo in api_result
                        if repo["has_issues"] and not repo["archived"]
                    }
                else:
                    response.raise_for_status()

        # Ensure sorted iteration later
        repos = {key: repos[key] for key in sorted(repos)}

        log.info("Discovered repositories on %s: %s", self.name, ", ".join(repos))

        return repos

    def get_base_url(self) -> str:
        return self.instance_api_url or f"{self.instance_url.rstrip('/')}/api/{self.API_VERSION}"
