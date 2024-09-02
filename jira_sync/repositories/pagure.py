"""
Module for communicating with pagure API.

See https://pagure.io/api/0
"""

from typing import Any

import requests

from .base import APIBase, Instance, Issue, IssueStatus, Repository


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
            if not url:
                return None
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
        _tags = api_result["tags"]

        if _assignee:
            assignee = _assignee["name"]
        else:
            assignee = None

        if _status != "closed":
            if self.blocked_label and self.blocked_label in _tags:
                status = IssueStatus.blocked
            else:
                if not _assignee:
                    status = IssueStatus.new
                else:
                    status = IssueStatus.assigned
        else:
            status = IssueStatus.closed

        return Issue(
            repository=self,
            full_url=full_url,
            title=title,
            content=content,
            assignee=assignee,
            status=status,
        )

    def get_issue_params(self) -> dict[str, Any]:
        if not self.label:
            return {}
        return {"params": {"tags": self.label}}


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
