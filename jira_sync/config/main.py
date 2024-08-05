"""Load, validate and normalize configuration."""

import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .legacy_model import LegacyConfig
from .model import Config


def transform_legacy_configuration(legacy_config: LegacyConfig) -> Config:
    """Generate current format configuration from legacy configuration.

    :param legacy_config: Legacy configuration object

    :return: Current format configuration object
    """
    config_raw: dict[str, Any] = {
        "general": {"jira": {}},
        "instances": {"pagure.io": {"type": "pagure"}},
    }

    config_raw["general"]["jira"]["instance_url"] = str(legacy_config.General.jira_instance)
    config_raw["general"]["jira"]["project"] = legacy_config.General.jira_project
    config_raw["general"]["jira"]["token"] = legacy_config.General.jira_token
    config_raw["general"]["jira"]["default_issue_type"] = (
        legacy_config.General.jira_default_issue_type
    )
    config_raw["general"]["jira"]["label"] = legacy_config.General.jira_label

    config_raw["general"]["jira"]["statuses"] = legacy_config.General.states.model_dump()

    pagure_raw = config_raw["instances"]["pagure.io"]
    pagure_raw["enabled"] = legacy_config.Pagure.enabled
    pagure_raw["instance_url"] = str(legacy_config.Pagure.pagure_url)
    pagure_raw["blocked_label"] = legacy_config.Pagure.blocked_label
    pagure_raw["usermap"] = legacy_config.Pagure.usernames
    pagure_raw["repositories"] = {
        repo["repo"]: {"label": repo["label"] or None} for repo in legacy_config.Pagure.repositories
    }

    return Config.model_validate(config_raw)


def load_configuration(config_path: Path | str) -> Config:
    """Load the configuration from a file.

    :param config_path: The path to the configuration file

    :return: a configuration dictionary
    """
    if isinstance(config_path, str):
        config_path = Path(config_path)

    with config_path.open("rb") as fp:
        config_raw = tomllib.load(fp)

    try:
        legacy_config = LegacyConfig.model_validate(config_raw)
    except ValidationError:
        config = Config.model_validate(config_raw)
    else:
        config = transform_legacy_configuration(legacy_config)

    for instance in config.instances.values():
        if isinstance(instance.usermap, Path):
            if not instance.usermap.root:
                usermap_path = config_path.resolve().parent / instance.usermap
            else:
                usermap_path = instance.usermap

            with usermap_path.open("rb") as usermap_file:
                instance.usermap = tomllib.load(usermap_file)

        for repo in instance.repositories.values():
            for inheritable in ("enabled", "blocked_label"):
                if getattr(repo, inheritable) is None:
                    setattr(repo, inheritable, getattr(instance, inheritable))

    return config
