import importlib.util
import os
import stat
import sys
from io import BytesIO
from pathlib import Path

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "migrations"
    / "teamspeak-channel-migration"
    / "teamspeak_channels.py"
)
SPEC = importlib.util.spec_from_file_location(
    "teamspeak_channel_migration", MODULE_PATH
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

QueryError = MODULE.QueryError
TS3Connection = MODULE.TS3Connection
channelcreate_command = MODULE.channelcreate_command
credential = MODULE.credential
normalize_channels = MODULE.normalize_channels
order_channels = MODULE.order_channels
parse_record = MODULE.parse_record
ts3_escape = MODULE.ts3_escape
ts3_unescape = MODULE.ts3_unescape
write_export = MODULE.write_export


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


def test_sensitive_query_omits_server_supplied_detail() -> None:
    class SocketStub:
        def sendall(self, _data: bytes) -> None:
            return None

    connection = object.__new__(TS3Connection)
    connection._socket = SocketStub()
    connection._reader = BytesIO(
        b"error id=520 msg=invalid\\slogin extra_msg=echoed-secret\n"
    )

    with pytest.raises(QueryError) as captured:
        connection.command(
            "login client_login_password=echoed-secret",
            "ServerQuery authentication",
            sensitive=True,
        )

    assert "echoed-secret" not in str(captured.value)


def test_credential_reads_named_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TS3_TEST_SECRET", "value")

    assert credential("TS3_TEST_SECRET", "Secret: ") == "value"


def test_export_refuses_to_replace_an_existing_file(tmp_path: Path) -> None:
    output = tmp_path / "channels.json"
    output.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError, match="already exists"):
        write_export(output, [{"cid": "1"}], force=False)

    assert output.read_text(encoding="utf-8") == "keep"


def test_export_creates_a_private_file_without_temp_artifacts(tmp_path: Path) -> None:
    output = tmp_path / "channels.json"

    write_export(output, [{"cid": "1"}], force=False)

    assert output.read_text(encoding="utf-8") == '[\n  {\n    "cid": "1"\n  }\n]\n'
    assert list(tmp_path.iterdir()) == [output]
    if os.name == "posix":
        assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_forced_export_replaces_the_file_atomically(tmp_path: Path) -> None:
    output = tmp_path / "channels.json"
    output.write_text("old", encoding="utf-8")

    write_export(output, [{"cid": "1"}], force=True)

    assert output.read_text(encoding="utf-8") == '[\n  {\n    "cid": "1"\n  }\n]\n'
