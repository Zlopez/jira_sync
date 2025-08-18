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


class BlockValuesConfig(BaseModel):
    true: int
    false: int


class JiraConfig(BaseModel):
    instance_url: HttpUrl
    project: str
    token: str
    default_issue_type: str
    label: str
    story_points_field: str = ""
    external_url_field: str
    blocked_field: str

    blocked_values: BlockValuesConfig

    statuses: StatusesConfig


class GeneralConfig(BaseModel):
    jira: JiraConfig


InlineUsermap = dict[str, str]


class RepoConfig(BaseModel):
    enabled: bool | None = None
    label: str | None = None
    blocked_label: str | None = None
    labels_to_story_points: dict[str, int] | None = None


class QueryRepoSpec(RepoConfig):
    enabled: bool = True
    labels_to_story_points: dict[str, int] | None = None


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
    labels_to_story_points: dict[str, int] = {}
    retrieve_closed_days_ago: int = 0
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


class GitLabQueryRepoSpecOrg(QueryRepoSpec):
    org: str


class GitLabQueryRepoSpecUser(QueryRepoSpec):
    user: str


GitLabQueryRepoSpec = GitLabQueryRepoSpecOrg | GitLabQueryRepoSpecUser


class GitLabConfig(InstanceConfigBase):
    type: Literal["gitlab"]
    instance_api_url: HttpUrl
    query_repositories: list[GitLabQueryRepoSpec] = []


class ForgejoQueryRepoSpecOrg(QueryRepoSpec):
    org: str


class ForgejoQueryRepoSpecUser(QueryRepoSpec):
    user: str


ForgejoQueryRepoSpec = ForgejoQueryRepoSpecOrg | ForgejoQueryRepoSpecUser


class ForgejoConfig(InstanceConfigBase):
    type: Literal["forgejo"]
    query_repositories: list[ForgejoQueryRepoSpec] = []


InstanceConfig = PagureConfig | GitHubConfig | GitLabConfig | ForgejoConfig
InstancesConfig = dict[str, InstanceConfig]


class Config(BaseModel):
    config_path: Path
    general: GeneralConfig
    instances: InstancesConfig
