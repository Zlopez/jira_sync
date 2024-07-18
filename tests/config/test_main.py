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
            "repositories": {
                "namespace/test1": {"blocked_label": "blocked", "enabled": True, "label": None},
                "test2": {"blocked_label": "blocked", "enabled": True, "label": "test"},
            },
            "type": "pagure",
            "usermap": {"pagure_user1": "jira_user1", "pagure_user2": "jira_user2"},
        }
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
def test_main(usermap_type: str, config_source: str, param_type: type, tmp_path):
    override = config_source == "repo"
    usermap = {"pagure_user1": "jira_user1", "pagure_user2": "jira_user2"}

    if usermap_type != "direct":
        usermap_file = tmp_path / "fedora_jira_usermap.toml"
        with usermap_file.open("w") as fp:
            tomlkit.dump(usermap, fp)

    with CONFIG_PATH.open("r") as fp:
        config_toml = tomlkit.load(fp)
        if "instances" in config_toml:
            for instance_def in config_toml["instances"].values():
                match usermap_type:
                    case "relative":
                        instance_def["usermap"] = "fedora_jira_usermap.toml"
                    case "absolute":
                        instance_def["usermap"] = str(tmp_path / "fedora_jira_usermap.toml")
                    case "direct":
                        instance_def["usermap"] = usermap

                if override:
                    for repo_def in instance_def["repositories"].values():
                        repo_def["enabled"] = choice((True, False))  # noqa: S311
                        repo_def["blocked_label"] = "Blocked, I say!"

    tmp_config_file = tmp_path / "config.toml"
    with tmp_config_file.open("w") as fp:
        tomlkit.dump(config_toml, fp)

    expected_config = deepcopy(EXPECTED_CONFIG)
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
