"""Pydantic model describing the configuration file."""

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, HttpUrl, model_validator

StatusKeys = Literal["new", "assigned", "blocked", "closed"]


class StatusesConfig(BaseModel):
    new: str
    assigned: str
    blocked: str
    closed: str


class JiraConfig(BaseModel):
    instance_url: HttpUrl
    project: str
    token: str
    default_issue_type: str
    label: str
    story_points_field: str

    statuses: StatusesConfig


class GeneralConfig(BaseModel):
    jira: JiraConfig


InlineUsermap = dict[str, str]


class RepoConfig(BaseModel):
    enabled: bool | None = None
    label: str | None = None
    blocked_label: str | None = None


class QueryRepoSpec(RepoConfig):
    enabled: bool = True


class InstanceConfigBase(BaseModel):
    type: str
    name: str | None = None
    instance_url: HttpUrl
    instance_api_url: HttpUrl | None = None
    enabled: bool = True
    token: str | None = None
    label: str | None = None
    blocked_label: str
    usermap: InlineUsermap | Path
    story_points: dict[str, int] = {}
    query_repositories: list = []
    repositories: dict[str, RepoConfig] = {}


class PagureQueryRepoSpec(QueryRepoSpec):
    pattern: str | None = None
    namespace: str | None = None

    @model_validator(mode="after")
    def check_spec_not_empty(self) -> Self:
        if not self.pattern and not self.namespace:
            raise ValueError("At least one of pattern, namespace has to be set")
        return self


class PagureConfig(InstanceConfigBase):
    type: Literal["pagure"]
    query_repositories: list[PagureQueryRepoSpec] = []


class GitHubQueryRepoSpecOrg(QueryRepoSpec):
    org: str


class GitHubQueryRepoSpecUser(QueryRepoSpec):
    user: str


GitHubQueryRepoSpec = GitHubQueryRepoSpecOrg | GitHubQueryRepoSpecUser


class GitHubConfig(InstanceConfigBase):
    type: Literal["github"]
    instance_api_url: HttpUrl
    query_repositories: list[GitHubQueryRepoSpec] = []


InstanceConfig = PagureConfig | GitHubConfig
InstancesConfig = dict[str, InstanceConfig]


class Config(BaseModel):
    config_path: Path
    general: GeneralConfig
    instances: InstancesConfig
