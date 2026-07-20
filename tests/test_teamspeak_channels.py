import pytest

from teamspeak.teamspeak_channels import (
    QueryError,
    channelcreate_command,
    credential,
    normalize_channels,
    order_channels,
    parse_record,
    ts3_escape,
    ts3_unescape,
)


def test_escape_round_trip() -> None:
    value = "Room name/one|two\\three\nnext"

    assert ts3_unescape(ts3_escape(value)) == value


def test_parse_record_decodes_query_values() -> None:
    record = parse_record(r"cid=4 channel_name=Ops\sRoom channel_flag_permanent=1")

    assert record == {
        "cid": "4",
        "channel_name": "Ops Room",
        "channel_flag_permanent": "1",
    }


def test_parent_is_ordered_before_child() -> None:
    channels = normalize_channels(
        [
            {"cid": "2", "pid": "1", "channel_name": "Child"},
            {"cid": "1", "pid": "0", "channel_name": "Parent"},
        ]
    )

    assert [item["cid"] for item in order_channels(channels)] == ["1", "2"]


def test_hierarchy_cycle_is_rejected() -> None:
    channels = normalize_channels(
        [
            {"cid": "1", "pid": "2", "channel_name": "One"},
            {"cid": "2", "pid": "1", "channel_name": "Two"},
        ]
    )

    with pytest.raises(ValueError, match="cycle"):
        order_channels(channels)


def test_channel_command_uses_mapped_parent_and_order() -> None:
    command = channelcreate_command(
        {
            "channel_name": "Ops Room",
            "channel_maxclients": "-1",
            "channel_flag_maxclients_unlimited": "1",
        },
        new_parent_id="20",
        new_order_id="21",
    )

    assert "channel_name=Ops\\sRoom" in command
    assert "channel_maxclients=" not in command
    assert "cpid=20" in command
    assert "channel_order=21" in command


def test_query_error_does_not_store_secret_command() -> None:
    error = QueryError("ServerQuery authentication", "520", "invalid login")

    assert "ServerQuery authentication" in str(error)
    assert "password" not in str(error)


def test_credential_reads_named_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TS3_TEST_SECRET", "value")

    assert credential("TS3_TEST_SECRET", "Secret: ") == "value"
