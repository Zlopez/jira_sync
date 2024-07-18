"""Load, validate and normalize configuration."""

import tomllib
from pathlib import Path

from .model import Config


def load_configuration(config_path: Path | str) -> Config:
    """Load the configuration from a file.

    :param config_path: The path to the configuration file

    :return: a configuration dictionary
    """
    if isinstance(config_path, str):
        config_path = Path(config_path)

    with config_path.open("rb") as fp:
        config_raw = tomllib.load(fp)

    config = Config.model_validate(config_raw)

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
