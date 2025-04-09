"""
API wrapper base for source code forges
"""

import logging
from collections.abc import Collection
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Annotated, Any, ClassVar, Self, Type
from weakref import ProxyType, proxy

import requests
from pydantic import AnyUrl

from ..config.model import InstanceConfig

log = logging.getLogger(__name__)


class IssueStatus(Enum):
    new = auto()
    assigned = auto()
    blocked = auto()
    closed = auto()


@dataclass(kw_only=True, frozen=True)
class Issue:
    """Metadata of issues in repositories.

    This is a generic “lowest common denominator” representation of issue
    metadata in remote repositories.
    """

    repository: "Repository"
    full_url: str
    title: str
    content: str
    assignee: str | None
    status: IssueStatus
    story_points: int


class APIBase:
    """Base class for communicating with web APIs."""

    _requests_params: ClassVar[set[str]] = {"url", "params", "data", "json", "headers", "cookies"}
    _api_result_selectors: ClassVar[dict[str, str]] = {}

    # Declare token here so it can be used in get_next_page(). Repository objects will dispatch
    # access to their instances.
    token: str | None

    @classmethod
    def sanitize_requests_params(cls, params: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in params.items() if key in cls._requests_params}

    def get_base_url(self) -> str:
        """Determine base url of an instance or repository in the instance."""
        raise NotImplementedError

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


class Repository(APIBase):
    """Proxy wrapping remote git repositories.

    This class is abstract, concrete git forge instances (for Pagure, GitHub,
    etc.) instantiate subclasses for the repositories they handle.
    """

    instance: Annotated[ProxyType, "Instance"]
    name: str
    _config_params: dict[str, Any]

    def __init__(self, instance: "Instance", name: str, **config_params: dict[str, Any]):
        # Avoid cyclical dependency
        self.instance = proxy(instance)
        self.name = name
        # Filter out unset configuration parameters
        self._config_params = {
            key: value for key, value in config_params.items() if value is not None
        }

    def __getattr__(self, key):
        if key not in self._config_params:
            return getattr(self.instance, key)
        return self._config_params[key]

    def get_issue_params(self, closed: bool) -> dict[str, Any]:
        """Get query parameters to select pertinent issues.

        :return: A dictionary with the necessary query parameters
        """
        raise NotImplementedError

    def get_issues(self, closed: bool = False) -> list[Issue]:
        """
        Retrieve all pertinent project issues on project.
        By default returns only open issues.

        :param closed: Retrieve closed issues.

        :return: List of issues
        """
        kwargs = self.get_issue_params(closed)

        issues: list[Issue] = []
        response = None
        first_call = True

        while next_page := self.get_next_page(endpoint="issues", response=response, **kwargs):
            response = requests.get(**next_page)
            if response.status_code == requests.codes.ok:
                api_result = response.json()
                if "issues" in self._api_result_selectors:
                    partial_issues = self.select_from_result(
                        api_result, self._api_result_selectors["issues"]
                    )
                else:
                    partial_issues = api_result

                # GitHub returns both pull requests and issues
                # Filter out the pull requests
                for issue in partial_issues:
                    if "pull_request" not in issue:
                        issues.append(self.normalize_issue(issue))
            elif first_call and response.status_code == requests.codes.not_found:
                # Pagure repos without issues enabled can’t be detected early, so we bow out
                # gracefully here.
                break
            else:
                response.raise_for_status()
            first_call = False

        if not closed:
            log.info(
                "Retrieved %s open issues from %s:%s", len(issues), self.instance.name, self.name
            )
        else:
            log.info(
                "Retrieved %s closed issues from %s:%s", len(issues), self.instance.name, self.name
            )

        return issues


class Instance(APIBase):
    """Proxy wrapping remote git forges.

    This class is abstract, its from_config() class method acts as a
    factory, dispatching to concrete subclasses which implement the
    functionality for concrete types of git forges.
    """

    _types_subclasses: ClassVar[dict[str, type]] = {}

    type: ClassVar[str]
    repo_cls: ClassVar[Type["Repository"]] = Repository

    name: str
    instance_url: str
    # Allow computing this from instance_url in a property
    _instance_api_url: str
    token: str | None
    blocked_label: str | None
    enabled: bool

    usermap: dict[str, str]
    repositories: dict[str, "Repository"]
    labels_to_story_points: dict[str, int]

    _query_repositories: Collection[dict[str, Any]]
    _repositories: dict[str, Any]

    def __init_subclass__(cls) -> None:
        """Register subclasses by `type` key."""
        if cls.type in cls._types_subclasses:
            raise TypeError(f"Duplicate subclass for type {cls.type}")  # pragma: no cover
        cls._types_subclasses[cls.type] = cls

    def __init__(
        self,
        *,
        name: str,
        instance_url: str | AnyUrl,
        instance_api_url: str | AnyUrl | None,
        enabled: bool,
        token: str | None,
        label: str | None,
        blocked_label: str | None,
        usermap: dict[str, str],
        query_repositories: Collection[dict[str, Any]],
        repositories: dict[str, dict[str, Any]],
        labels_to_story_points: dict[str, int],
        retrieve_closed_days_ago: int,
        **kwargs,
    ) -> None:
        """
        Initialize an Instance object.

        :param name: Name of the instance
        :param instance_url: (Root) URL of the server hosting the instance
        :param instance_api_url: Optional (Root) URL of the API server hosting the
            repository
        :param enabled: If the repository is enabled or not
        :param token: API token for the instance
        :param label: Label to retrieve
        :param blocked_label: Label that marks the issue as blocked
        :param usermap: Mapping of forge usernames to JIRA usernames
        :param query_repositories: Configuration for querying repositories by namespace
        :param repositories: Mapping of repository names to configuration
        :param labels_to_story_poinsts: Mapping of labels to story points values
        :param retrieve_closed_days_ago: How much ago we should look for closed tickets
        """
        self.name = name

        if not isinstance(instance_url, str):
            instance_url = str(instance_url)
        self.instance_url = instance_url.rstrip("/")

        if instance_api_url:
            if not isinstance(instance_api_url, str):
                instance_api_url = str(instance_api_url)
            self.instance_api_url = instance_api_url.rstrip("/")

        self.enabled = enabled
        self.token = token
        self.label = label
        self.blocked_label = blocked_label

        self.usermap = usermap
        self.labels_to_story_points = labels_to_story_points or {}
        self.retrieve_closed_days_ago = retrieve_closed_days_ago or 0
        self._query_repositories = query_repositories or ()
        self._repositories = repositories or {}
        repo_specs: dict[str, dict[str, Any]] = (
            self.query_repositories() if query_repositories else {}
        ) | repositories
        self.repositories = {
            name: self.repo_cls(instance=self, name=name, **repo_spec)
            for name, repo_spec in repo_specs.items()
        }

        super().__init__()

    @classmethod
    def from_config(cls, name: str, config_path: Path, config: InstanceConfig) -> Self:
        """Create an Instance object from a configuration dictionary.

        :param name: Name of the instance
        :param config_path: Path to the configuration file
        :param config: Pydantic model configuring the instance
        :return: The created Instance object
        """
        kwargs = config.model_dump()
        kwargs["name"] = name
        return cls._types_subclasses[config.type](config_path=config_path, **kwargs)

    def query_repositories(self) -> dict[str, dict[str, Any]]:
        """Query repositories in bulk from an instance.

        :returns: The repository names/paths and their configurations on this
            instance.
        """
        raise NotImplementedError

    def get_base_url(self) -> str:
        return self.instance_api_url or self.instance_url

    @property
    def instance_api_url(self) -> str | None:
        return getattr(self, "_instance_api_url", None)

    @instance_api_url.setter
    def instance_api_url(self, value: str) -> None:
        self._instance_api_url = value
