import copy
import importlib.util
import io
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest

TOOL = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "semaphore_project_reconciler", TOOL / "reconcile_semaphore.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

TOKEN = "test-token-value"
KEY = "-----BEGIN PRIVATE KEY-----\nfixture-only\n-----END PRIVATE KEY-----\n"


class FakeAPI:
    """In-memory HTTP transport used by the real API client."""

    def __init__(self):
        self.collections = {"/projects": []}
        self.requests = []
        self.next_id = 1
        self.failure = None
        self.error_body = b"request rejected"
        self.expired = False

    def add(self, path, payload):
        item = {**copy.deepcopy(payload), "id": self.next_id}
        self.next_id += 1
        self.collections.setdefault(path, []).append(item)
        return item

    def open(self, request, timeout):
        assert timeout > 0
        assert request.get_header("Authorization") == f"Bearer {TOKEN}"
        parsed = urlparse(request.full_url)
        assert parsed.hostname == "127.0.0.1"
        path = parsed.path.removeprefix("/api")
        method = request.get_method()
        payload = json.loads(request.data) if request.data else None
        self.requests.append((method, path, payload))
        if self.failure == (method, path):
            raise HTTPError(
                request.full_url, 400, "rejected", {}, io.BytesIO(self.error_body)
            )
        if method == "GET":
            result = self.collections[path]
        elif method == "POST":
            result = self.add(path, payload)
            if path == "/projects":
                prefix = f"/project/{result['id']}"
                for name in (
                    "keys",
                    "repositories",
                    "inventory",
                    "environment",
                    "views",
                    "templates",
                ):
                    self.collections[f"{prefix}/{name}"] = []
                self.add(f"{prefix}/views", {"title": "All", "position": 0})
                self.add(f"{prefix}/keys", {"name": "None", "type": "none"})
        elif method == "DELETE" and path.startswith("/user/tokens/"):
            self.expired = True
            result = None
        else:
            collection, identifier = path.rsplit("/", 1)
            items = self.collections[collection]
            current = next(item for item in items if item["id"] == int(identifier))
            if method == "PUT":
                current.update(payload)
            else:
                assert method == "DELETE"
                items.remove(current)
            result = None
        return io.BytesIO(json.dumps(result).encode())

    @property
    def writes(self):
        return [request for request in self.requests if request[0] != "GET"]


@pytest.fixture
def manifest():
    return MODULE.load_manifest(TOOL / "manifest.yml.example")


@pytest.fixture
def runtime(monkeypatch, tmp_path, manifest):
    api = FakeAPI()
    monkeypatch.setattr(MODULE, "build_opener", lambda *handlers: api)
    monkeypatch.setenv("TEST_API_TOKEN", TOKEN)
    monkeypatch.setenv("TEST_SSH_KEY", KEY)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    args = [
        "--token-env",
        "TEST_API_TOKEN",
        "--private-key-env",
        "TEST_SSH_KEY",
        str(path),
    ]
    return api, args


def test_default_plan_changes_nothing(runtime, capsys):
    api, args = runtime
    before = copy.deepcopy(api.collections)
    assert MODULE.main(args) == 2
    assert api.collections == before
    assert api.writes == []
    assert "mode=plan" in capsys.readouterr().out
    assert not MODULE.build_parser().parse_args([]).apply
    assert not MODULE.build_parser().parse_args([]).prune


def test_apply_creates_linked_objects_and_second_plan_has_no_drift(runtime, manifest):
    api, args = runtime
    assert MODULE.main([*args, "--apply"]) == 0
    templates = api.collections["/project/1/templates"]
    assert len(templates) == len(manifest["templates"])
    for template in templates:
        for field, collection in (
            ("repository_id", "repositories"),
            ("inventory_id", "inventory"),
            ("view_id", "views"),
        ):
            assert template[field] in {
                item["id"] for item in api.collections[f"/project/1/{collection}"]
            }
        assert template["environment_ids"] == [
            api.collections["/project/1/environment"][0]["id"]
        ]
    api.requests.clear()
    assert MODULE.main(args) == 0
    assert api.writes == []


def test_existing_drift_is_read_only_until_apply(runtime):
    api, args = runtime
    assert MODULE.main([*args, "--apply"]) == 0
    api.collections["/project/1/templates"][0]["description"] = "stale"
    before = copy.deepcopy(api.collections)
    api.requests.clear()
    assert MODULE.main(args) == 2
    assert api.collections == before
    assert not api.writes
    assert MODULE.main([*args, "--apply"]) == 0
    assert len(api.writes) == 1
    assert api.writes[0][0] == "PUT"
    assert MODULE.main(args) == 0


@pytest.mark.parametrize(
    "flag,phrase",
    [
        ("--prune", MODULE.PRUNE_PHRASE),
        ("--refresh-credential", MODULE.REFRESH_PHRASE),
    ],
)
def test_destructive_apply_requires_exact_confirmation(runtime, flag, phrase):
    api, args = runtime
    assert MODULE.main([*args, "--apply", flag]) == 1
    assert MODULE.main([*args, "--apply", flag, "--confirm", "wrong"]) == 1
    assert api.requests == []
    assert MODULE.main([*args, "--apply", flag, "--confirm", phrase]) == 0


def test_prune_retains_other_resources_and_deletes_templates_before_views(runtime):
    api, args = runtime
    assert MODULE.main([*args, "--apply"]) == 0
    extra_view = api.add("/project/1/views", {"title": "Manual", "position": 9})
    extra_template = api.add(
        "/project/1/templates", {"name": "Manual", "view_id": extra_view["id"]}
    )
    repository = api.add("/project/1/repositories", {"name": "Manual"})
    project = api.add("/projects", {"name": "Manual"})
    api.requests.clear()
    assert MODULE.main(args) == 0
    assert MODULE.main([*args, "--prune"]) == 2
    assert not api.writes
    assert (
        MODULE.main([*args, "--apply", "--prune", "--confirm", MODULE.PRUNE_PHRASE])
        == 0
    )
    assert [(method, path) for method, path, _ in api.writes] == [
        ("DELETE", f"/project/1/templates/{extra_template['id']}"),
        ("DELETE", f"/project/1/views/{extra_view['id']}"),
    ]
    assert repository in api.collections["/project/1/repositories"]
    assert project in api.collections["/projects"]


def test_refresh_is_opt_in_and_uses_named_key(runtime):
    api, args = runtime
    assert MODULE.main([*args, "--apply"]) == 0
    api.requests.clear()
    assert MODULE.main([*args, "--apply"]) == 0
    assert not api.writes
    assert (
        MODULE.main(
            [
                *args,
                "--apply",
                "--refresh-credential",
                "--confirm",
                MODULE.REFRESH_PHRASE,
            ]
        )
        == 0
    )
    assert len(api.writes) == 1
    assert api.writes[0][2]["ssh"]["private_key"] == KEY


def test_missing_key_prevents_all_writes(runtime, monkeypatch):
    api, args = runtime
    monkeypatch.delenv("TEST_SSH_KEY")
    assert MODULE.main([*args, "--apply"]) == 1
    assert not api.writes
    assert MODULE.main(args) == 2


def test_token_comes_only_from_named_variable(runtime, monkeypatch, capsys):
    api, args = runtime
    monkeypatch.delenv("TEST_API_TOKEN")
    monkeypatch.setenv(MODULE.DEFAULT_TOKEN_ENV, TOKEN)
    assert MODULE.main(args) == 1
    assert api.requests == []
    assert TOKEN not in capsys.readouterr().err
    with pytest.raises(ValueError, match="secrets belong"):
        MODULE.parse_config({"semaphore": {"token": TOKEN}})
    with pytest.raises(SystemExit):
        MODULE.build_parser().parse_args(["--token-file", "token.txt"])


def test_explicit_expiration_runs_even_after_partial_failure(runtime, capsys):
    api, args = runtime
    api.failure = ("POST", "/project/1/repositories")
    assert MODULE.main([*args, "--apply", "--expire-token"]) == 1
    assert api.expired
    output = capsys.readouterr()
    assert "partial-apply" in output.err
    assert "created project" in output.out
    assert "created repository" not in output.out
    assert TOKEN not in output.out + output.err
    assert KEY not in output.out + output.err


def test_credential_error_body_is_not_printed(runtime, capsys):
    api, args = runtime
    api.failure = ("POST", "/project/1/keys")
    api.error_body = KEY.encode()
    assert MODULE.main([*args, "--apply"]) == 1
    assert "fixture-only" not in capsys.readouterr().err


def test_error_redacts_token_before_truncating(runtime, capsys):
    api, args = runtime
    api.failure = ("GET", "/projects")
    api.error_body = ("x" * 295 + TOKEN).encode()
    assert MODULE.main(args) == 1
    assert "test-" not in capsys.readouterr().err


def test_missing_all_view_fails_closed(runtime, capsys):
    api, args = runtime
    assert MODULE.main([*args, "--apply"]) == 0
    api.collections["/project/1/views"] = []
    api.requests.clear()
    assert MODULE.main(args) == 1
    assert "missing its All view" in capsys.readouterr().err
    assert not api.writes


def test_config_precedence_and_relative_paths(tmp_path):
    config = tmp_path / "config.local.toml"
    config.write_text("""[semaphore]
url = "https://semaphore.example.net"
token_env = "CONFIG_TOKEN"
timeout_seconds = 12
ca_file = "ca.pem"
[credential]
login = "operator"
private_key_env = "CONFIG_KEY"
[manifests]
paths = ["project.json"]
""")
    defaults = MODULE.load_config(None)
    assert defaults.url == MODULE.DEFAULT_URL
    assert defaults.timeout == 30
    loaded = MODULE.load_config(config)
    assert loaded.manifests == (tmp_path / "project.json",)
    assert loaded.ca_file == tmp_path / "ca.pem"
    assert (
        MODULE.resolve_settings(loaded, MODULE.build_parser().parse_args([])) == loaded
    )
    args = MODULE.build_parser().parse_args(
        [
            "--url",
            "http://127.0.0.1:3000",
            "--token-env",
            "CLI_TOKEN",
            "--timeout",
            "3",
            "--no-ca-file",
            "--credential-login",
            "deploy",
            "--private-key-env",
            "CLI_KEY",
            "other.json",
        ]
    )
    resolved = MODULE.resolve_settings(loaded, args)
    assert resolved.url == MODULE.DEFAULT_URL
    assert resolved.token_env == "CLI_TOKEN"
    assert resolved.timeout == 3
    assert resolved.ca_file is None
    assert resolved.credential_login == "deploy"
    assert resolved.private_key_env == "CLI_KEY"
    assert resolved.manifests == (Path("other.json"),)


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout(timeout):
    with pytest.raises(ValueError, match="timeout"):
        MODULE.validate_settings(MODULE.Settings(timeout=timeout))


def test_configurator_non_overwrite_and_secret_exclusion(tmp_path, monkeypatch):
    monkeypatch.setenv(MODULE.DEFAULT_TOKEN_ENV, TOKEN)
    monkeypatch.setenv(MODULE.DEFAULT_PRIVATE_KEY_ENV, KEY)
    output = tmp_path / "config.local.toml"
    command = [sys.executable, str(TOOL / "configure.py"), "--output", str(output)]
    first = subprocess.run(command, capture_output=True, text=True, check=False)
    assert first.returncode == 0, first.stderr
    contents = output.read_text()
    MODULE.parse_config(tomllib.loads(contents), tmp_path)
    assert "CUSTOMIZE:" in contents
    assert TOKEN not in contents + first.stdout
    assert KEY not in contents + first.stdout
    second = subprocess.run(command, capture_output=True, text=True, check=False)
    assert second.returncode == 1
    assert "refusing to replace" in second.stderr
    assert output.read_text() == contents


@pytest.mark.parametrize("script", ["reconcile_semaphore.py", "configure.py"])
def test_help(script):
    result = subprocess.run(
        [sys.executable, str(TOOL / script), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--token-env" in result.stdout


@pytest.mark.parametrize(
    "case,message",
    [
        ("duplicate_template", "duplicate template"),
        ("duplicate_view", "duplicate view"),
        ("missing_all", "starting with All"),
        ("unknown_view", "unknown view"),
        ("arguments", "string list"),
        ("unknown_key", "unknown keys"),
        ("missing_path", "missing path"),
        ("variables", "map names to strings"),
        ("survey", "must be true or false"),
    ],
)
def test_manifest_validation_errors(manifest, case, message):
    if case == "duplicate_template":
        manifest["templates"].append(copy.deepcopy(manifest["templates"][0]))
    elif case == "duplicate_view":
        manifest["views"].append("All")
    elif case == "missing_all":
        manifest["views"].pop(0)
    elif case == "unknown_view":
        manifest["templates"][0]["view"] = "Missing"
    elif case == "arguments":
        manifest["templates"][0]["arguments"] = "--check"
    elif case == "unknown_key":
        manifest["templates"][0]["argument"] = ["--check"]
    elif case == "missing_path":
        del manifest["project"]["repository"]["path"]
    elif case == "variables":
        manifest["project"]["environment"]["variables"]["LANG"] = 1
    else:
        manifest["templates"][0]["survey"] = [{"name": "target", "required": "yes"}]
    with pytest.raises(MODULE.ManifestError, match=message):
        MODULE.validate_manifest(manifest, "fixture")


def test_all_manifests_validated_before_any_write(runtime, tmp_path):
    api, args = runtime
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{}")
    assert MODULE.main([*args, str(invalid), "--apply"]) == 1
    assert not api.requests


def test_duplicate_projects_rejected(runtime):
    api, args = runtime
    assert MODULE.main([*args, args[-1], "--apply"]) == 1
    assert not api.requests


def test_offline_check_needs_no_token_or_transport(runtime, monkeypatch):
    api, args = runtime
    monkeypatch.delenv("TEST_API_TOKEN")
    assert MODULE.main([*args, "--check-manifests"]) == 0
    assert not api.requests


def test_environment_payload_is_deterministic():
    payload = MODULE.environment_payload(
        7, {"name": "Locale", "variables": {"LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}}
    )
    assert payload["project_id"] == 7
    assert payload["env"] == '{"LANG":"C.UTF-8","LC_ALL":"C.UTF-8"}'
    assert payload["json"] == "{}"
    assert payload["password"] is None
    assert payload["secrets"] == []


def test_template_payload_and_survey_normalization():
    payload = MODULE.template_payload(
        project_id=4,
        template={
            "name": "Example",
            "playbook": "check.yml",
            "arguments": ["--check"],
            "survey": [{"name": "target", "type": "", "values": [], "required": True}],
        },
        repository_id=11,
        inventory_id=12,
        environment_id=13,
        view_id=14,
    )
    assert payload["project_id"] == 4
    assert payload["repository_id"] == 11
    assert payload["inventory_id"] == 12
    assert payload["environment_ids"] == [13]
    assert payload["view_id"] == 14
    assert payload["arguments"] == '["--check"]'
    assert payload["app"] == "ansible"
    assert payload["survey_vars"] == [{"name": "target", "required": True}]


def test_resource_diff_compares_json_semantically():
    desired = {
        "arguments": '["--check"]',
        "survey_vars": [],
        "allow_parallel_tasks": False,
    }
    current = {
        "arguments": '[ "--check" ]',
        "survey_vars": None,
        "allow_parallel_tasks": None,
    }
    assert not MODULE.resource_changed(
        current, desired, fields=desired, json_fields=("arguments", "survey_vars")
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "stale"),
        ("git_branch", "old"),
        ("type", "build"),
        ("start_version", "1.0.0"),
        ("autorun", True),
        ("task_params", '{"stale":true}'),
        ("vaults", '[{"id":99}]'),
    ],
)
def test_diff_covers_previously_omitted_fields(field, value):
    desired = MODULE.template_payload(
        project_id=1,
        template={"name": "Example", "playbook": "check.yml"},
        repository_id=2,
        inventory_id=3,
        environment_id=4,
        view_id=5,
    )
    current = {**desired, field: value}
    assert MODULE.resource_changed(
        current,
        desired,
        fields=MODULE.TEMPLATE_COMPARE_FIELDS,
        json_fields=MODULE.TEMPLATE_JSON_FIELDS,
    )


def test_unmanaged_detection_uses_ids():
    reconciler = MODULE.Reconciler(
        None,
        apply=False,
        private_key=None,
        credential_login="ansible",
        refresh_credential=False,
        prune=False,
        out=io.StringIO(),
    )
    reconciler.report_unmanaged(
        "project", [{"id": 1, "name": "Duplicate"}, {"id": 2, "name": "Duplicate"}], {1}
    )
    assert [str(action) for action in reconciler.actions] == [
        "unmanaged project Duplicate (id 2) retained"
    ]


def test_redirect_handler_rejects_forwarding():
    assert (
        MODULE.RejectRedirects().redirect_request(
            None, None, 302, "", {}, "https://other.example.net"
        )
        is None
    )
