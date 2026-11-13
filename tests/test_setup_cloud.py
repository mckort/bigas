"""Cloud Run setup planning. The runner is fake; nothing calls gcloud."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from bigas.setup_cloud import CommandResult, missing_deploy_requirements, setup_cloud, subprocess_runner


def _runner(responses):
    calls = []

    def run(args):
        args = list(args)
        calls.append(args)
        for predicate, result in responses:
            try:
                matched = predicate(args)
            except IndexError:
                matched = False
            if matched:
                return result
        return CommandResult(0, "", "")

    return calls, run


def test_dry_run_does_not_call_gcloud(tmp_path):
    calls, run = _runner([])
    code = setup_cloud(project="acme-bigas", apply=False, deploy=False, env_file=tmp_path / ".env", runner=run)
    assert code == 0
    assert calls == []


def test_apply_creates_missing_pieces_and_fills_empty_env(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=gem\nGOOGLE_PROJECT_ID=keep-me\n", encoding="utf-8")
    calls, run = _runner(
        [
            (lambda args: args[1:3] == ["auth", "list"], CommandResult(0, "me@example.com\n")),
            (lambda args: args[1] == "billing", CommandResult(0, "True\n")),
            (lambda args: "describe" in args, CommandResult(1, "", "not found")),
            (lambda args: args[1] == "firestore" and "list" in args, CommandResult(0, "")),
        ]
    )
    code = setup_cloud(project="acme-bigas", apply=True, deploy=False, env_file=env, runner=run)
    assert code == 0
    text = env.read_text(encoding="utf-8")
    assert "GOOGLE_PROJECT_ID=keep-me" in text
    assert "GOOGLE_SERVICE_ACCOUNT_EMAIL=bigas-runner@acme-bigas.iam.gserviceaccount.com" in text
    assert "DOCKER_REPO=bigas-repo" in text
    assert any(args[1:4] == ["artifacts", "repositories", "create"] for args in calls)
    assert any(args[1:4] == ["iam", "service-accounts", "create"] for args in calls)
    assert any(args[1:3] == ["firestore", "databases"] and "create" in args for args in calls)
    assert not any(str(args[0]).endswith("deploy.sh") for args in calls)


def test_apply_skips_create_when_resources_exist(tmp_path):
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    calls, run = _runner(
        [
            (lambda args: args[1:3] == ["auth", "list"], CommandResult(0, "me@example.com\n")),
            (lambda args: args[1] == "billing", CommandResult(0, "True\n")),
            (lambda args: "describe" in args, CommandResult(0, "exists\n")),
            (lambda args: args[1] == "firestore" and "list" in args, CommandResult(0, "(default)\n")),
        ]
    )
    assert setup_cloud(project="acme-bigas", apply=True, deploy=False, env_file=env, runner=run) == 0
    assert not any("create" in args for args in calls)


def test_missing_billing_stops_before_any_create(tmp_path):
    calls, run = _runner(
        [
            (lambda args: args[1:3] == ["auth", "list"], CommandResult(0, "me@example.com\n")),
            (lambda args: args[1] == "billing", CommandResult(0, "False\n")),
        ]
    )
    code = setup_cloud(project="acme-bigas", apply=True, deploy=False, env_file=tmp_path / ".env", runner=run)
    assert code == 2
    assert [args[1] for args in calls] == ["auth", "billing"]


def test_deploy_runs_after_requirements_are_met(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "GEMINI_API_KEY=gem",
                "GA4_PROPERTY_ID=123",
                "GOOGLE_PROJECT_ID=acme-bigas",
                "GOOGLE_SERVICE_ACCOUNT_EMAIL=bigas-runner@acme-bigas.iam.gserviceaccount.com",
                "DOCKER_REPO=bigas-repo",
                "IMAGE_NAME=bigas",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    calls, run = _runner(
        [
            (lambda args: args[1:3] == ["auth", "list"], CommandResult(0, "me@example.com\n")),
            (lambda args: args[1] == "billing", CommandResult(0, "True\n")),
            (lambda args: "describe" in args, CommandResult(0, "exists\n")),
            (lambda args: args[1] == "firestore" and "list" in args, CommandResult(0, "(default)\n")),
        ]
    )
    assert setup_cloud(project="acme-bigas", apply=True, deploy=True, env_file=env, runner=run, repo_root=tmp_path) == 0
    assert any(str(args[0]).endswith("deploy.sh") for args in calls)
    assert missing_deploy_requirements({"GEMINI_API_KEY": "gem"}) == [
        "GA4_PROPERTY_ID",
        "GOOGLE_PROJECT_ID",
        "GOOGLE_SERVICE_ACCOUNT_EMAIL",
        "DOCKER_REPO",
        "IMAGE_NAME",
    ]


def _gcloud_ready():
    return [
        (lambda args: args[1:3] == ["auth", "list"], CommandResult(0, "me@example.com\n")),
        (lambda args: args[1] == "billing", CommandResult(0, "True\n")),
        (lambda args: "describe" in args, CommandResult(0, "exists\n")),
        (lambda args: args[1] == "firestore" and "list" in args, CommandResult(0, "(default)\n")),
    ]


def test_subprocess_runner_reports_a_missing_command():
    result = subprocess_runner(["bigas-missing-command-xyz"])
    assert result.returncode == 127
    assert result.stdout == ""
    assert result.stderr


def test_deploy_reads_shell_environment_and_lets_the_file_win(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "GA4_PROPERTY_ID=123",
                "GOOGLE_PROJECT_ID=acme-bigas",
                "GOOGLE_SERVICE_ACCOUNT_EMAIL=bigas-runner@acme-bigas.iam.gserviceaccount.com",
                "DOCKER_REPO=bigas-repo",
                "IMAGE_NAME=bigas",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    for name in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "SECRET_MANAGER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "from-shell")
    monkeypatch.setenv("GA4_PROPERTY_ID", "")
    calls, run = _runner(_gcloud_ready())
    assert setup_cloud(project="acme-bigas", apply=True, deploy=True, env_file=env, runner=run, repo_root=tmp_path) == 0
    assert any(str(args[0]).endswith("deploy.sh") for args in calls)


def test_deploy_syncs_secrets_with_this_interpreter(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "GEMINI_API_KEY=gem",
                "GA4_PROPERTY_ID=123",
                "GOOGLE_PROJECT_ID=acme-bigas",
                "GOOGLE_SERVICE_ACCOUNT_EMAIL=bigas-runner@acme-bigas.iam.gserviceaccount.com",
                "DOCKER_REPO=bigas-repo",
                "IMAGE_NAME=bigas",
                "SECRET_MANAGER=true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    calls, run = _runner(_gcloud_ready())
    assert setup_cloud(project="acme-bigas", apply=True, deploy=True, env_file=env, runner=run, repo_root=tmp_path) == 0
    sync = [args for args in calls if any(str(part).endswith("sync_env_to_secret_manager.py") for part in args)]
    assert sync == [[sys.executable, str(tmp_path / "scripts" / "sync_env_to_secret_manager.py")]]


def test_deploy_failure_prints_the_last_lines(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "GEMINI_API_KEY=gem",
                "GA4_PROPERTY_ID=123",
                "GOOGLE_PROJECT_ID=acme-bigas",
                "GOOGLE_SERVICE_ACCOUNT_EMAIL=bigas-runner@acme-bigas.iam.gserviceaccount.com",
                "DOCKER_REPO=bigas-repo",
                "IMAGE_NAME=bigas",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    stderr = "\n".join(f"ERR{i:02d}" for i in range(12))
    calls, run = _runner(
        _gcloud_ready()
        + [(lambda args: str(args[0]).endswith("deploy.sh"), CommandResult(1, "stdout-only", stderr))]
    )
    assert setup_cloud(project="acme-bigas", apply=True, deploy=True, env_file=env, runner=run, repo_root=tmp_path) == 1
    assert any(str(args[0]).endswith("deploy.sh") for args in calls)
    out = capsys.readouterr().out
    assert "ERR02" in out
    assert "ERR11" in out
    assert "ERR00" not in out
    assert "ERR01" not in out
    assert "stdout-only" not in out


def test_deploy_flag_requires_apply(capsys):
    path = Path(__file__).resolve().parents[1] / "scripts" / "setup_cloud.py"
    spec = importlib.util.spec_from_file_location("setup_cloud_script", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--project", "acme-bigas", "--deploy"]) == 2
    assert "--deploy requires --apply" in capsys.readouterr().out
