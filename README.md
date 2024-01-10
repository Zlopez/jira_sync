# JIRA sync script

This script is created to sync issues from various ticket trackers to JIRA in unified way.
Supports https://pagure.io/ with another sources planned.

## Installation

1. Clone this repository
2. Make sure you have [poetry](https://python-poetry.org/) installed in your system
3. Install the script with `poetry install`

## Quickstart

1. Rename `config.example.toml` to `config.toml`
2. Fill `config.toml` with correct values (See [Configuration section](#configuration))
3. Run the script with `poetry run python jira_sync/main.py sync-tickets`

## Configuration

Configuration for `jira_sync` is provided by configuration file in [TOML format](https://toml.io/en/).
By default `jira_sync` is using `config.toml` file in working directory. You can provide any other configuration file by using `--config` parameter. Config file is split to multiple sections, which are explained bellow.

### General

General configuration for the whole script. It contains JIRA related configuration values and map
of states from source tickets to JIRA states `General.states`. The `jira_sync` script currently recognizes 4 different states in source ticket tracker:

* `new` - ticket is open and nobody is assigned to it
* `assigned` - ticket is open and assigned to user
* `blocked` - ticket has label marking it as blocked
* `closed` - ticket is closed

### Pagure

Configuration section for pagure source instance. You can disable/enable the whole pagure backend here `enabled`, specify which label is representing blocked ticket `blocked_label`, set the instance URL `pagure_url` and specify the repositories to sync `repositories`. It also contains map of pagure usernames to JIRA usernames `pagure.usernames`.

#### Adding a new repository

To add a new repository to synced ones just add line to repositories like this:

```
{ repo = "example", label = ""}
```

If you set `pagure_url` to https://pagure.io/ then this will make script to sync all open tickets from `https://pagure.io/example` project. If you want to sync only open tickets with specific label
just set `label` to some value.

## How the sync works

1. Obtain open tickets from source repository (for example from pagure)
2. Obtain open tickets from JIRA corresponding to source repository (based on label)
3. Match them together based on the URL link in JIRA ticket description field
4. If the match was not found, try to match source ticket against closed JIRA ticket
5. If the match is still not found, create the ticket in JIRA
6. Assign the ticket to correct user in JIRA (set the user to Unassigned if the user is not in the username map or the ticket is not assigned)
7. Transition the ticket to correct state based on the `General.state` configuration (don't move to new state from unknown states)
8. Set labels (each JIRA ticket gets `General.jira_label` and label corresponding to `repo` value in `repositories` list)
9. Close any JIRA ticket that was not matched in step 3
