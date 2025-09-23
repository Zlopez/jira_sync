import json
import logging
from pathlib import Path
from unittest import mock

import pytest
from fedora_messaging.config import conf
from fedora_messaging.message import load_message

from jira_sync.consumer import Consumer

from .common import PROJECT_ROOT


@pytest.fixture
def fm_config_file(tmp_path):
    config_path = PROJECT_ROOT / "config.example.toml"
    with mock.patch.dict(conf, {"consumer_config": {"config_file": config_path}}):
        yield config_path


@pytest.fixture
def mock_jira(test_jira_obj):
    with mock.patch("jira_sync.consumer.JIRA") as JIRA:
        JIRA.return_value = test_jira_obj
        yield test_jira_obj


@pytest.fixture
def consumer(fm_config_file, mock_jira, test_config, caplog):
    caplog.set_level(logging.INFO)
    with mock.patch("jira_sync.consumer.load_configuration") as load_configuration:
        load_configuration.return_value = test_config
        return Consumer()


def _message_fixture(filename):
    filepath = Path(__file__).parent.joinpath("fixtures").joinpath(filename)
    with open(filepath, "rb") as fh:
        return json.load(fh)


def test_consume_create(consumer, caplog):
    message = load_message(_message_fixture("msg_bz_mirror_add.json"))
    consumer(message)
    assert consumer._jira.get_issues_by_labels.call_count == 2
    consumer._jira.get_issues_by_labels.assert_any_call(
        filters=['"BZ URL" = "https://bugzilla.redhat.com/show_bug.cgi?id=2396373"']
    )
    consumer._jira.get_issues_by_labels.assert_any_call(
        filters=['summary ~ "https://bugzilla.redhat.com/show_bug.cgi?id=2396373"']
    )
    consumer._jira.create_issue(
        url="https://bugzilla.redhat.com/show_bug.cgi?id=2396373",
        labels=["label", "bugzilla", "distribution"],
    )
    assert "Creating JIRA ticket from Bugzilla ticket 2396373" in caplog.messages


def test_consume_already_created(consumer, caplog):
    message_dump = _message_fixture("msg_bz_mirror_add.json")
    message_dump["body"]["bug"]["id"] = 2396374
    message_dump["body"]["bug"]["weburl"] = message_dump["body"]["bug"]["weburl"].replace(
        "id=2396373", "id=2396374"
    )
    message = load_message(message_dump)
    consumer(message)
    assert consumer._jira.get_issues_by_labels.call_count == 2
    consumer._jira.get_issues_by_labels.assert_any_call(
        filters=['"BZ URL" = "https://bugzilla.redhat.com/show_bug.cgi?id=2396374"']
    )
    consumer._jira.get_issues_by_labels.assert_any_call(
        filters=['summary ~ "https://bugzilla.redhat.com/show_bug.cgi?id=2396374"']
    )
    consumer._jira.create_issue.assert_not_called()
    assert "BZ #2396374 has already been mirrored to CPE-201" in caplog.messages


def test_consume_already_created_not_watson_synced(consumer, caplog):
    message_dump = _message_fixture("msg_bz_mirror_add.json")
    message_dump["body"]["bug"]["id"] = 2396375
    message_dump["body"]["bug"]["weburl"] = message_dump["body"]["bug"]["weburl"].replace(
        "id=2396373", "id=2396375"
    )
    message = load_message(message_dump)
    consumer(message)
    assert consumer._jira.get_issues_by_labels.call_count == 2
    consumer._jira.create_issue.assert_not_called()
    assert "BZ #2396375 has already been mirrored to CPE-202" in caplog.messages


def test_consume_ignore(consumer, caplog):
    message = load_message(_message_fixture("msg_bz_new.json"))
    consumer(message)
    consumer._jira.get_issues_by_labels.assert_not_called
    assert "Not a BZ event we're interested in" in caplog.messages


def test_consume_exception(consumer, caplog):
    message_dump = _message_fixture("msg_bz_mirror_add.json")
    # Break the message
    del message_dump["body"]["event"]
    message = load_message(message_dump)
    consumer(message)
    assert consumer._jira.get_issues_by_labels.call_count == 0
    assert f"Handling of {message.id} failed!" in caplog.messages
    assert len(caplog.records) == 1
    assert caplog.records[0].levelname == "ERROR"
    assert "KeyError" in caplog.records[0].exc_text


def test_consume_create_failed(consumer, caplog):
    message = load_message(_message_fixture("msg_bz_mirror_add.json"))
    consumer._jira.create_issue.side_effect = lambda *a, **kw: None
    consumer(message)
    assert "Couldn’t create new JIRA issue from Bugzilla ticket 2396373" in caplog.messages
