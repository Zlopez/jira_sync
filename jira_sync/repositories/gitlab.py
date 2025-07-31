"""
Module for communicating with the GitLab REST API.

See https://docs.gitlab.com/18.2/api/rest/
"""

import datetime as dt
import logging
import urllib
from typing import Any, ClassVar

import requests
from requests.utils import parse_header_links

from .base import APIBase, Instance, Issue, IssueStatus, Repository

log = logging.getLogger(__name__)


class GitLabBase(APIBase):
    API_VERSION: ClassVar[str] = "18.2"

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
            _params.pop("per_page", None)

            for link in parse_header_links(response.headers["link"]):
                if link["rel"] == "next":
                    url = link["url"]
                    break
            else:
                return None
        else:
            _params.setdefault("per_page", "100")
            if endpoint:
                endpoint = "/" + endpoint.lstrip("/")
            else:
                endpoint = ""

            url = f"{self.get_base_url()}{endpoint}"

        _headers = {}

        if self.token:
            _headers["Authorization"] = f"Bearer {self.token}"

        if headers:
            _headers |= headers

        kwargs["headers"] = _headers
        kwargs["params"] = _params

        return self.sanitize_requests_params(kwargs | {"url": url})


class GitLabRepository(GitLabBase, Repository):
    """Wrapper class around the GitLab REST API for a single repository."""

    def get_base_url(self) -> str:
        encoded_url = urllib.parse.quote_plus(self.name)
        return self.instance.instance_api_url + f"/projects/{encoded_url}"

    def normalize_issue(self, api_result: dict[str, Any]) -> Issue:
        full_url = api_result["web_url"]
        title = api_result["title"]
        content = api_result["description"]
        _assignee = api_result["assignee"]
        _state = api_result["state"].lower()
        _labels = [
            label["name"] if isinstance(label, dict) else label for label in api_result["labels"]
        ]

        if _assignee:
            assignee = _assignee["username"]
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

        for tag in _labels:
            if tag in self.labels_to_story_points.keys():
                story_points = max(story_points, self.labels_to_story_points[tag])

        return Issue(
            repository=self,
            full_url=full_url,
            title=title,
            content=content,
            assignee=assignee,
            status=status,
            story_points=story_points,
        )

    def get_issue_params(self, closed: bool) -> dict[str, Any]:
        params = {}
        if closed:
            params = {
                "state": "closed",
                "updated_after": (
                    dt.datetime.now(dt.UTC) - dt.timedelta(days=self.retrieve_closed_days_ago)
                ).isoformat(timespec="seconds"),  # We need to format to ISO 8601
            }
        else:
            params = {"state": "opened"}
        if not self.label:
            return {"params": params}
        else:
            params["labels"] = self.label
        return {"params": params}


class GitLabInstance(GitLabBase, Instance):
    """Wrapper class around the GitLab REST API for the whole instance."""

    type = "gitlab"
    repo_cls = GitLabRepository

    def query_repositories(self) -> dict[str, dict[str, Any]]:
        """Query repositores in bulk from GitLab.

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
                    encoded_url = urllib.parse.quote_plus(org)
                    endpoint = f"/groups/{encoded_url}/projects"
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
                        if repo["issues_enabled"] and not repo["archived"]
                    }
                else:
                    response.raise_for_status()

        # Ensure sorted iteration later
        repos = {key: repos[key] for key in sorted(repos)}

        log.info("Discovered repositories on %s: %s", self.name, ", ".join(repos))

        return repos
