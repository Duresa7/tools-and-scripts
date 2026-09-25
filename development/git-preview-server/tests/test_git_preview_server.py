import http.client
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1]
SERVE = TOOL / "serve.mjs"
CONFIGURE = TOOL / "configure.mjs"
NODE_INSTALL = (
    "Install Node.js 20 or newer from https://nodejs.org/ and add node to PATH."
)
GIT_INSTALL = "Install Git from https://git-scm.com/downloads and add git to PATH."
LISTENING = re.compile(r"^listening: http://127\.0\.0\.1:(\d+)/$")

TRACKED = {
    "index.html": "<!doctype html><title>index</title>\n",
    "diagrams/flow.svg": '<svg xmlns="http://www.w3.org/2000/svg"></svg>\n',
    "docs/space name.html": "<!doctype html><title>space</title>\n",
    ".hidden.txt": "tracked dotfile\n",
    ".config/settings.json": '{"tracked": "inside a dot directory"}\n',
    ".gitignore": "ignored/\n",
}
VISIBLE_COUNT = 3


def require_command(name: str, install: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        pytest.fail(f"required command not found: {name}. {install}", pytrace=False)
    return executable


@pytest.fixture(scope="session")
def node() -> str:
    executable = require_command("node", NODE_INSTALL)
    version = subprocess.run(
        [executable, "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    if int(version.lstrip("v").split(".")[0]) < 20:
        pytest.fail(f"node {version} is too old. {NODE_INSTALL}", pytrace=False)
    return executable


@pytest.fixture(scope="session")
def git() -> str:
    return require_command("git", GIT_INSTALL)


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        env.pop(key, None)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    # Keeps git from finding a parent repository around the temporary folder.
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path.resolve())
    return env


def run_git(git: str, repo: Path, env: dict[str, str], *arguments: str) -> None:
    subprocess.run(
        [git, "-C", str(repo), *arguments],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repository(tmp_path: Path, git: str, environment: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for relative, content in TRACKED.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(
        [git, "-c", "init.defaultBranch=main", "init", "-q", str(repo)],
        env=environment,
        check=True,
        capture_output=True,
    )
    run_git(git, repo, environment, "add", "-A")
    run_git(
        git,
        repo,
        environment,
        "-c",
        "user.name=Preview Test",
        "-c",
        "user.email=preview@example.com",
        "commit",
        "-q",
        "-m",
        "fixture",
    )
    (repo / "untracked.txt").write_text("untracked content\n", encoding="utf-8")
    (repo / "ignored").mkdir()
    (repo / "ignored" / "secret.txt").write_text("ignored content\n", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside content\n", encoding="utf-8")
    return repo


def run_tool(
    node: str,
    script: Path,
    *arguments: str,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [node, str(script), *arguments],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


@contextmanager
def running_server(
    node: str, *arguments: str, cwd: Path, env: dict[str, str]
) -> Iterator[tuple[int, subprocess.Popen[str]]]:
    process = subprocess.Popen(
        [node, str(SERVE), "--port", "0", *arguments],
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line.rstrip("\n"))
        lines.put(None)

    threading.Thread(target=pump, daemon=True).start()
    try:
        port = None
        deadline = time.monotonic() + 15
        while port is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError("server did not report a listening address")
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty:
                continue
            if line is None:
                assert process.stderr is not None
                raise AssertionError(f"server exited early: {process.stderr.read()}")
            match = LISTENING.match(line)
            if match:
                port = int(match.group(1))
        yield port, process
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def fetch(
    port: int, target: str, *, method: str = "GET", host: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        headers = {} if host is None else {"Host": host}
        connection.request(method, target, headers=headers)
        response = connection.getresponse()
        body = response.read()
        return (
            response.status,
            {name.lower(): value for name, value in response.getheaders()},
            body,
        )
    finally:
        connection.close()


def statuses(port: int, targets: list[str]) -> dict[str, int]:
    return {target: fetch(port, target)[0] for target in targets}


def test_tracked_files_are_served_without_caching(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, _):
        status, headers, body = fetch(port, "/index.html")
        svg_status, svg_headers, _ = fetch(port, "/diagrams/flow.svg")
        space_status, _, space_body = fetch(port, "/docs/space%20name.html")

    assert status == 200
    assert body.decode() == TRACKED["index.html"]
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert headers["cache-control"] == "no-store"
    assert headers["x-content-type-options"] == "nosniff"
    assert svg_status == 200
    assert svg_headers["content-type"] == "image/svg+xml; charset=utf-8"
    assert space_status == 200
    assert space_body.decode() == TRACKED["docs/space name.html"]


def test_untracked_ignored_and_dotfile_paths_return_404(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    targets = [
        "/untracked.txt",
        "/ignored/secret.txt",
        "/.hidden.txt",
        "/.config/settings.json",
        "/.gitignore",
        "/.git/config",
        "/.git/HEAD",
        "/missing.html",
        "/docs",
        "/docs/",
    ]
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, _):
        observed = statuses(port, targets)
        _, _, body = fetch(port, "/ignored/secret.txt")

    assert observed == dict.fromkeys(targets, 404)
    assert b"ignored content" not in body


def test_traversal_and_encoded_paths_return_404(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    outside = (repository.parent / "outside.txt").resolve().as_posix()
    targets = [
        "/docs/../index.html",
        "/./index.html",
        "/docs/../untracked.txt",
        "/../outside.txt",
        "/%2e%2e/outside.txt",
        "/docs/%2e%2e/%2e%2e/outside.txt",
        "/docs/..%2f..%2foutside.txt",
        "/docs/..%5c..%5coutside.txt",
        "/docs\\..\\..\\outside.txt",
        "/" + outside,
        "/index.html%00.svg",
        "/diagrams//flow.svg",
    ]
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, _):
        observed = statuses(port, targets)
        malformed_status = fetch(port, "/%E0%A4%A")[0]

    assert observed == dict.fromkeys(targets, 404)
    assert malformed_status == 400


def test_root_path_reports_rule_and_visible_count(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, _):
        status, headers, body = fetch(port, "/")
        query_status = fetch(port, "/?page=1")[0]

    text = body.decode()
    assert status == 200
    assert query_status == 200
    assert headers["content-type"] == "text/plain; charset=utf-8"
    assert f"currently visible: {VISIBLE_COUNT} files" in text
    assert "git ls-files" in text
    assert str(repository) not in text


def test_tracking_changes_apply_without_restart(
    node: str, git: str, repository: Path, environment: dict[str, str]
) -> None:
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, _):
        before = statuses(port, ["/untracked.txt", "/index.html"])
        run_git(git, repository, environment, "add", "untracked.txt")
        run_git(git, repository, environment, "rm", "-q", "--cached", "index.html")
        after = statuses(port, ["/untracked.txt", "/index.html"])

    assert before == {"/untracked.txt": 404, "/index.html": 200}
    assert after == {"/untracked.txt": 200, "/index.html": 404}


def test_git_failure_fails_closed(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, process):
        assert fetch(port, "/index.html")[0] == 200
        (repository / ".git" / "index").write_bytes(b"not a git index")
        status, _, body = fetch(port, "/index.html")
        root_status = fetch(port, "/")[0]
        process.terminate()
        process.wait(timeout=10)
        assert process.stderr is not None
        stderr = process.stderr.read()

    assert status == 500
    assert body == b"unable to read tracked files\n"
    assert root_status == 500
    assert "git-error:" in stderr


def test_host_header_must_be_an_ip_literal_or_localhost(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, _):
        observed = {
            host: fetch(port, "/index.html", host=host)[0]
            for host in (
                f"127.0.0.1:{port}",
                f"localhost:{port}",
                f"LOCALHOST:{port}",
                f"[::1]:{port}",
                "127.0.0.1",
                f"preview.example.com:{port}",
                f"localhost.:{port}",
                "127.0.0.1:not-a-port",
                "[::1",
            )
        }
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.putrequest("GET", "/index.html", skip_host=True)
            connection.endheaders()
            missing = connection.getresponse()
            missing_status = missing.status
            missing_body = missing.read()
        finally:
            connection.close()

    assert observed == {
        f"127.0.0.1:{port}": 200,
        f"localhost:{port}": 200,
        f"LOCALHOST:{port}": 200,
        f"[::1]:{port}": 200,
        "127.0.0.1": 200,
        f"preview.example.com:{port}": 403,
        f"localhost.:{port}": 403,
        "127.0.0.1:not-a-port": 403,
        "[::1": 403,
    }
    assert missing_status in {400, 403}
    assert b"index" not in missing_body


def test_only_get_and_head_are_accepted(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, _):
        post_status, post_headers, _ = fetch(port, "/index.html", method="POST")
        head_status, head_headers, head_body = fetch(port, "/index.html", method="HEAD")

    assert post_status == 405
    assert post_headers["allow"] == "GET, HEAD"
    assert head_status == 200
    assert head_body == b""
    assert head_headers["content-length"] == str(len(TRACKED["index.html"]))


def test_symbolic_links_resolve_only_to_tracked_paths_inside_root(
    node: str, git: str, repository: Path, environment: dict[str, str]
) -> None:
    elsewhere = repository.parent / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "page.html").write_text("outside page\n", encoding="utf-8")
    (repository / "swap").mkdir()
    (repository / "swap" / "page.html").write_text("inside page\n", encoding="utf-8")
    try:
        (repository / "alias.html").symlink_to("index.html")
        (repository / "outside-link.txt").symlink_to(repository.parent / "outside.txt")
        (repository / "ignored-link.txt").symlink_to(Path("ignored") / "secret.txt")
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable here: {exc}")
    run_git(git, repository, environment, "add", "-A")
    # The index still lists swap/page.html after the folder becomes a link.
    shutil.rmtree(repository / "swap")
    (repository / "swap").symlink_to(elsewhere, target_is_directory=True)

    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, _):
        observed = statuses(
            port,
            [
                "/alias.html",
                "/outside-link.txt",
                "/ignored-link.txt",
                "/swap/page.html",
            ],
        )

    assert observed == {
        "/alias.html": 200,
        "/outside-link.txt": 404,
        "/ignored-link.txt": 404,
        "/swap/page.html": 404,
    }


def test_interrupt_stops_the_server_cleanly(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    if os.name == "nt":
        pytest.skip("POSIX signal delivery is required")
    with running_server(
        node, "--root", str(repository), cwd=repository, env=environment
    ) as (port, process):
        assert fetch(port, "/index.html")[0] == 200
        process.send_signal(signal.SIGINT)
        assert process.wait(timeout=10) == 0


def test_non_loopback_host_requires_the_separate_flag(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    refused_preview = run_tool(
        node, SERVE, "--host", "0.0.0.0", "--dry-run", cwd=repository, env=environment
    )
    refused_live = run_tool(
        node, SERVE, "--host", "192.0.2.10", cwd=repository, env=environment
    )
    accepted = run_tool(
        node,
        SERVE,
        "--host",
        "0.0.0.0",
        "--allow-non-loopback",
        "--dry-run",
        cwd=repository,
        env=environment,
    )

    for refused in (refused_preview, refused_live):
        assert refused.returncode == 1
        assert "--allow-non-loopback" in refused.stderr
        assert "listen" not in refused.stdout
    assert accepted.returncode == 0, accepted.stderr
    assert "warning:" in accepted.stderr
    assert "every interface" in accepted.stderr
    assert "would-listen: http://0.0.0.0:8123/" in accepted.stdout


def test_host_must_be_an_ip_literal(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    completed = run_tool(
        node, SERVE, "--host", "localhost", "--dry-run", cwd=repository, env=environment
    )

    assert completed.returncode == 1
    assert "IP address" in completed.stderr


def test_dry_run_defaults_to_git_top_level_of_current_directory(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    completed = run_tool(
        node, SERVE, "--dry-run", cwd=repository / "docs", env=environment
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.splitlines() == [
        f"root: {repository.resolve()}",
        "would-listen: http://127.0.0.1:8123/",
        f"servable-files: {VISIBLE_COUNT}",
        "dry-run: no port was opened",
    ]


def test_command_line_overrides_local_config(
    node: str, git: str, tmp_path: Path, repository: Path, environment: dict[str, str]
) -> None:
    other = tmp_path / "other"
    subprocess.run(
        [git, "init", "-q", str(other)],
        env=environment,
        check=True,
        capture_output=True,
    )
    config = tmp_path / "config.local.json"
    config.write_text(
        json.dumps({"root": str(repository), "host": "::1", "port": 8124}),
        encoding="utf-8",
    )

    from_config = run_tool(
        node, SERVE, "--config", str(config), "--dry-run", cwd=tmp_path, env=environment
    )
    from_command_line = run_tool(
        node,
        SERVE,
        "--config",
        str(config),
        "--root",
        str(other),
        "--host",
        "127.0.0.2",
        "--port",
        "8125",
        "--dry-run",
        cwd=tmp_path,
        env=environment,
    )

    assert from_config.returncode == 0, from_config.stderr
    assert f"root: {repository.resolve()}" in from_config.stdout
    assert "would-listen: http://[::1]:8124/" in from_config.stdout
    assert from_command_line.returncode == 0, from_command_line.stderr
    assert f"root: {other.resolve()}" in from_command_line.stdout
    assert "would-listen: http://127.0.0.2:8125/" in from_command_line.stdout


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"hostname": "127.0.0.1"}, "unknown key: hostname"),
        ({"allow_non_loopback": True}, "only on the command line"),
        ({"root": "relative/path"}, "absolute path"),
        ({"port": "8123"}, "port must be an integer"),
        ({"port": 70000}, "port must be an integer"),
        ({"host": "example.com"}, "IP address"),
        ([], "JSON object"),
    ],
)
def test_invalid_config_is_rejected(
    node: str,
    tmp_path: Path,
    repository: Path,
    environment: dict[str, str],
    payload: object,
    message: str,
) -> None:
    config = tmp_path / "config.local.json"
    config.write_text(json.dumps(payload), encoding="utf-8")

    completed = run_tool(
        node,
        SERVE,
        "--config",
        str(config),
        "--dry-run",
        cwd=repository,
        env=environment,
    )

    assert completed.returncode == 1
    assert message in completed.stderr


def test_root_must_be_the_top_level_of_a_working_tree(
    node: str, tmp_path: Path, repository: Path, environment: dict[str, str]
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    subdirectory = run_tool(
        node,
        SERVE,
        "--root",
        str(repository / "docs"),
        "--dry-run",
        cwd=tmp_path,
        env=environment,
    )
    not_repository = run_tool(
        node, SERVE, "--root", str(plain), "--dry-run", cwd=tmp_path, env=environment
    )

    assert subdirectory.returncode == 1
    assert "not the top level" in subdirectory.stderr
    assert not_repository.returncode == 1
    assert "not inside a git working tree" in not_repository.stderr


def test_unknown_option_is_rejected(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    completed = run_tool(node, SERVE, "--listen-all", cwd=repository, env=environment)

    assert completed.returncode == 1
    assert "--listen-all" in completed.stderr


def test_help_names_inputs_and_safety_flags(
    node: str, tmp_path: Path, environment: dict[str, str]
) -> None:
    serve_help = run_tool(node, SERVE, "--help", cwd=tmp_path, env=environment)
    configure_help = run_tool(node, CONFIGURE, "--help", cwd=tmp_path, env=environment)

    assert serve_help.returncode == 0, serve_help.stderr
    for fragment in ("--root", "--host", "--port", "--allow-non-loopback", "--dry-run"):
        assert fragment in serve_help.stdout
    assert configure_help.returncode == 0, configure_help.stderr
    assert "--output" in configure_help.stdout
    assert "refuses to replace" in configure_help.stdout


def test_configurator_writes_config_and_refuses_overwrite(
    node: str, tmp_path: Path, repository: Path, environment: dict[str, str]
) -> None:
    output = tmp_path / "config.local.json"
    command = ("--output", str(output), "--root", str(repository), "--port", "8130")

    first = run_tool(node, CONFIGURE, *command, cwd=tmp_path, env=environment)
    written = output.read_text(encoding="utf-8")
    second = run_tool(
        node, CONFIGURE, *command[:2], "--port", "9000", cwd=tmp_path, env=environment
    )
    preview = run_tool(
        node, SERVE, "--config", str(output), "--dry-run", cwd=tmp_path, env=environment
    )

    assert first.returncode == 0, first.stderr
    assert f"configuration-written: {output}" in first.stdout
    payload = json.loads(written)
    assert payload["root"] == str(repository.resolve())
    assert payload["host"] == "127.0.0.1"
    assert payload["port"] == 8130
    assert written.count("CUSTOMIZE:") == 4
    assert second.returncode == 1
    assert "refusing to replace" in second.stderr
    assert output.read_text(encoding="utf-8") == written
    assert preview.returncode == 0, preview.stderr
    assert "would-listen: http://127.0.0.1:8130/" in preview.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ("--root", "docs"),
        ("--host", "example.com"),
        ("--port", "70000"),
        ("--port", "-1"),
    ],
)
def test_configurator_rejects_invalid_input_without_writing(
    node: str,
    tmp_path: Path,
    repository: Path,
    environment: dict[str, str],
    arguments: tuple[str, str],
) -> None:
    output = tmp_path / "config.local.json"

    completed = run_tool(
        node,
        CONFIGURE,
        "--output",
        str(output),
        *arguments,
        cwd=repository,
        env=environment,
    )

    assert completed.returncode == 1
    assert completed.stderr.startswith("error:")
    assert not output.exists()


def test_configurator_notes_that_a_non_loopback_host_stays_gated(
    node: str, tmp_path: Path, repository: Path, environment: dict[str, str]
) -> None:
    output = tmp_path / "config.local.json"

    written = run_tool(
        node,
        CONFIGURE,
        "--output",
        str(output),
        "--host",
        "192.0.2.10",
        cwd=repository,
        env=environment,
    )
    preview = run_tool(
        node,
        SERVE,
        "--config",
        str(output),
        "--dry-run",
        cwd=repository,
        env=environment,
    )

    assert written.returncode == 0, written.stderr
    assert "--allow-non-loopback" in written.stdout
    assert json.loads(output.read_text(encoding="utf-8"))["root"] == ""
    assert preview.returncode == 1
    assert "--allow-non-loopback" in preview.stderr


def test_example_config_marks_every_value_and_runs_unchanged(
    node: str, repository: Path, environment: dict[str, str]
) -> None:
    example = TOOL / "config.example.json"
    payload = json.loads(example.read_text(encoding="utf-8"))

    completed = run_tool(
        node,
        SERVE,
        "--config",
        str(example),
        "--dry-run",
        cwd=repository,
        env=environment,
    )

    assert example.read_text(encoding="utf-8").count("CUSTOMIZE:") == 4
    assert {key for key in payload if not key.startswith("_comment")} == {
        "root",
        "host",
        "port",
    }
    assert completed.returncode == 0, completed.stderr
    assert "would-listen: http://127.0.0.1:8123/" in completed.stdout
