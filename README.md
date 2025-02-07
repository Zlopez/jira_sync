# JIRA sync script

This script is created to sync issues from various ticket trackers to JIRA in unified way.
Supports https://pagure.io/ with another sources planned.

## Features

* Sync tickets from Pagure and GitHub to JIRA
* Check for closed tickets X days ago
* Add story points to JIRA ticket based on labels on original tickets
* Add assignee to ticket from map
* Filter tickets to sync by labels
* Filter tickets to sync by repository names/namespace
* Add link to upstream ticket to ticket description
* Mapping of states between original ticket and JIRA
* Move ticket to blocked state based on label on original ticket

## Installation

1. Clone this repository
2. Make sure you have [poetry](https://python-poetry.org/) installed in your system
3. Install the script with `poetry install`

## Quickstart

1. Rename `config.example.toml` to `config.toml`
2. Fill `config.toml` with correct values (See [Configuration section](#configuration))
3. Run the script with `poetry run jira_sync sync-tickets`

## Configuration

Configuration for `jira_sync` is provided by configuration file in [TOML format](https://toml.io/en/).
By default `jira_sync` is using `config.toml` file in working directory. You can provide any other configuration file by using `--config` parameter. Config file is split to multiple sections, see https://github.com/Zlopez/jira_sync/blob/main/config.example.toml for more info.
