"""Pydantic model describing the configuration file."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, HttpUrl

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

    statuses: StatusesConfig


class GeneralConfig(BaseModel):
    jira: JiraConfig


InlineUsermap = dict[str, str]


class RepoConfig(BaseModel):
    enabled: bool | None = None
    label: str | None = None
    blocked_label: str | None = None


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
    repositories: dict[str, RepoConfig]


class PagureConfig(InstanceConfigBase):
    type: Literal["pagure"]


class GitHubConfig(InstanceConfigBase):
    type: Literal["github"]
    instance_api_url: HttpUrl


InstanceConfig = PagureConfig | GitHubConfig
InstancesConfig = dict[str, InstanceConfig]


class Config(BaseModel):
    config_path: Path
    general: GeneralConfig
    instances: InstancesConfig
