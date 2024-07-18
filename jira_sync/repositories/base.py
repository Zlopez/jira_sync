"""
API wrapper base for source code forges
"""

import logging
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, ClassVar, Self

import requests
from jira import Issue as JiraIssue
from pydantic import AnyUrl

log = logging.getLogger(__name__)


class IssueStatus(Enum):
    new = auto()
    assigned = auto()
    blocked = auto()
    closed = auto()


@dataclass(kw_only=True, frozen=True)
class Issue:
    """Metadata of issues in repositories.

    This is a generic “lowest common denominator” represenation of issue
    metadata in remote repositories.
    """

    repository: "Repository"
    full_url: str
    title: str
    content: str
    assignee: str | None
    status: IssueStatus
    jira_issue: JiraIssue | None = None


class Repository:
    """Abstract wrapper class around API calls to git repositories."""

    _types_subclasses: ClassVar[dict[str, type]] = {}
    _requests_params: ClassVar[set[str]] = {"url", "params", "data", "json", "headers", "cookies"}
    _api_result_selectors: ClassVar[dict[str, str]] = {}

    type: ClassVar[str]

    instance_url: str
    instance_api_url: str | None
    repo: str
    enabled: bool
    token: str | None
    blocked_label: str | None
    usermap: dict[str, str]

    def __init_subclass__(cls) -> None:
        """Register subclasses by `type` key."""
        if cls.type in cls._types_subclasses:
            raise TypeError(f"Duplicate subclass for type {cls.type}")  # pragma: no cover
        cls._types_subclasses[cls.type] = cls

    def __init__(
        self,
        *,
        instance_url: str | AnyUrl,
        instance_api_url: str | AnyUrl | None,
        repo: str,
        enabled: bool,
        token: str | None,
        label: str | None,
        blocked_label: str | None,
        usermap: dict[str, str],
        **kwargs,
    ) -> None:
        """
        Initialize a Repository object.

        :param instance_url: (Base) URL of the server hosting the repository
        :param instance_api_url: (Base) URL of the API server hosting the
            repository
        :param repo: Name or path of the repository
        :param enabled: If the repository is enabled or not
        :param usermap: Mapping of repository usernames to JIRA usernames
        """
        if not isinstance(instance_url, str):
            instance_url = str(instance_url)
        self.instance_url = instance_url.rstrip("/")

        if instance_api_url:
            if not isinstance(instance_api_url, str):
                instance_api_url = str(instance_api_url)
            self.instance_api_url = instance_api_url.rstrip("/")
        else:
            self.instance_api_url = None

        self.repo = repo
        self.enabled = enabled
        self.token = token
        self.label = label
        self.blocked_label = blocked_label

        self.usermap = usermap

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> Self:
        """Create a Repository object from a configuration dictionary.

        :param config: Dictionary which configures the repository
        :return: The created Repository object
        """
        return cls._types_subclasses[config["type"]](**config)

    @classmethod
    def sanitize_requests_params(cls, params: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in params.items() if key in cls._requests_params}

    def get_next_page(
        self,
        *,
        endpoint: str | None = None,
        response: requests.Response | None = None,
        **kwargs,
    ) -> dict[str, Any] | None:
        """Determine URL and other arguments for next page

        :param endpoint: Optional endpoint beneath that of a repository
        :param params: Optional dictionary to pass on query parameters
        :param response: Optional requests.Response representing a previous
            API result (which might contain information about the next page)

        :return: A dictionary containing the `url` and optionally `params`,
            `headers` to use with requests.get(), or None if no next page
            exists.
        """
        raise NotImplementedError

    @staticmethod
    def select_from_result(result: dict[str, Any], selector: str | None) -> Any:
        """Extract a specific piece from an API result.

        An API result often contains data along with metadata, e.g. for pagination. Extract
        the pertinent pieces.

        :param api_result: A dictionary containing the result from the API
        :param selector: Optional, dotted "path" pointing at the payload

        :return: A normalized issue
        """
        if not selector:
            return result

        for subselector in selector.split("."):
            result = result[subselector]

        return result

    def normalize_issue(self, api_result: dict[str, Any]) -> Issue:
        """Normalize API result into Issue object.

        :param api_result: The JSON result return from the API

        :return: The Issue object
        """
        raise NotImplementedError

    def get_issue_params(self) -> dict[str, Any]:
        """Get query parameters to select pertinent issues.

        :return: A dictionary with the necessary query parameters
        """
        raise NotImplementedError

    def get_open_issues(self) -> list[Issue]:
        """
        Retrieve all pertinent open project issues on project.

        :return: List of issues
        """
        kwargs = self.get_issue_params()
        next_page = self.get_next_page(endpoint="issues", **kwargs)

        issues: list[Issue] = []

        while next_page:
            response = requests.get(**next_page)
            if response.status_code == requests.codes.ok:
                api_result = response.json()
                if "issues" in self._api_result_selectors:
                    partial_issues = self.select_from_result(
                        api_result, self._api_result_selectors["issues"]
                    )
                else:
                    partial_issues = api_result

                issues.extend(self.normalize_issue(issue) for issue in partial_issues)
                next_page = self.get_next_page(endpoint="issues", response=response, **kwargs)

        log.info("Retrieved %s open issues from %s:%s", len(issues), self.instance_url, self.repo)

        return issues
