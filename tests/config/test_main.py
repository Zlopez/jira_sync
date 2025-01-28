from copy import deepcopy
from pathlib import Path
from random import choice

import pytest
import tomlkit

from jira_sync.config import main

PROJECT_ROOT = Path(__file__).parent.parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.example.toml"
EXPECTED_CONFIG = {
    "general": {
        "jira": {
            "default_issue_type": "Story",
            "instance_url": "https://jira.atlassian.com/",
            "label": "label",
            "project": "Project",
            "story_points_field": "",
            "statuses": {
                "assigned": "IN_PROGRESS",
                "blocked": "BLOCKED",
                "closed": "DONE",
                "new": "NEW",
            },
            "token": "token",
        }
    },
    "instances": {
        "pagure.io": {
            "blocked_label": "blocked",
            "enabled": True,
            "token": None,
            "instance_api_url": None,
            "instance_url": "https://pagure.io/",
            "label": None,
            "story_points": {
                "label1": 1,
                "label2": 5,
                "label3": 10,
            },
            "query_repositories": [
                {
                    "blocked_label": None,
                    "enabled": True,
                    "label": "test",
                    "namespace": "fedora-infra",
                    "pattern": None,
                },
            ],
            "repositories": {
                "namespace/test1": {"blocked_label": "blocked", "enabled": True, "label": None},
                "test2": {"blocked_label": "blocked", "enabled": True, "label": "test"},
            },
            "type": "pagure",
            "usermap": {"pagure_user1": "jira_user1", "pagure_user2": "jira_user2"},
        },
        "github.com": {
            "blocked_label": "blocked",
            "enabled": True,
            "token": None,
            "instance_api_url": "https://api.github.com/",
            "instance_url": "https://github.com/",
            "label": None,
            "story_points": {},
            "query_repositories": [
                {
                    "blocked_label": None,
                    "enabled": True,
                    "label": "test",
                    "org": "fedora-infra",
                },
            ],
            "repositories": {
                "org/test1": {"blocked_label": "blocked", "enabled": True, "label": None},
                "test2": {"blocked_label": "blocked", "enabled": True, "label": "test"},
            },
            "type": "github",
            "usermap": {"github_user1": "jira_user1", "github_user2": "jira_user2"},
        },
    },
}


@pytest.mark.parametrize(
    "usermap_type, config_source",
    (
        ("relative", "instance"),
        ("relative", "repo"),
        ("absolute", "instance"),
        ("absolute", "repo"),
        ("direct", "instance"),
        ("direct", "repo"),
    ),
)
@pytest.mark.parametrize("param_type", (str, Path))
def test_load_configuration(usermap_type: str, config_source: str, param_type: type, tmp_path):
    override = config_source == "repo"
    usermaps = {
        "pagure.io": {"pagure_user1": "jira_user1", "pagure_user2": "jira_user2"},
        "github.com": {"github_user1": "jira_user1", "github_user2": "jira_user2"},
    }
    usermap_files = {}

    if usermap_type != "direct":
        for instance_name in ("pagure.io", "github.com"):
            usermap_files[instance_name] = tmp_path / f"{instance_name}_jira_usermap.toml"
            with usermap_files[instance_name].open("w") as fp:
                tomlkit.dump(usermaps[instance_name], fp)

    with CONFIG_PATH.open("r") as fp:
        config_toml = tomlkit.load(fp)
        for instance_name, instance_def in config_toml["instances"].items():
            match usermap_type:
                case "relative":
                    instance_def["usermap"] = f"{instance_name}_jira_usermap.toml"
                case "absolute":
                    instance_def["usermap"] = str(tmp_path / f"{instance_name}_jira_usermap.toml")
                case "direct":
                    instance_def["usermap"] = usermaps[instance_name]

            if override:
                for repo_def in instance_def["repositories"].values():
                    repo_def["enabled"] = choice((True, False))  # noqa: S311
                    repo_def["blocked_label"] = "Blocked, I say!"

    tmp_config_file = tmp_path / "config.toml"
    with tmp_config_file.open("w") as fp:
        tomlkit.dump(config_toml, fp)

    expected_config = deepcopy(EXPECTED_CONFIG) | {"config_path": str(tmp_config_file)}
    for instance in expected_config["instances"].values():
        # Unset in configuration
        instance["name"] = None

    if override:
        for instance_name, instance_def in expected_config["instances"].items():
            for repo_name, repo_def in instance_def["repositories"].items():
                for inheritable in ("enabled", "blocked_label"):
                    repo_def[inheritable] = config_toml["instances"][instance_name]["repositories"][
                        repo_name
                    ][inheritable]

    config_model = main.load_configuration(param_type(tmp_config_file))

    config_raw = config_model.model_dump(mode="json")
    assert config_raw == expected_config
