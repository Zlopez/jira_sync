"""Pydantic model describing the legacy configuration file."""

from typing import Literal

from pydantic import BaseModel, HttpUrl


class StatesConfig(BaseModel):
    new: str
    assigned: str
    blocked: str
    closed: str


class GeneralConfig(BaseModel):
    jira_instance: HttpUrl
    jira_project: str
    jira_token: str
    jira_default_issue_type: str
    jira_label: str

    states: StatesConfig


RepositoryConfigKey = Literal["repo", "label"]


class PagureConfig(BaseModel):
    enabled: bool
    pagure_url: HttpUrl
    blocked_label: str
    repositories: list[dict[RepositoryConfigKey, str]]
    usernames: dict[str, str]


class LegacyConfig(BaseModel):
    General: GeneralConfig
    Pagure: PagureConfig
