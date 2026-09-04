"""Tests for self-healing CI/CD (BIG-10)."""
from __future__ import annotations

import io
import json
import os
import zipfile

import pytest

os.environ.setdefault("GA4_PROPERTY_ID", "test-property")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("GITHUB_TOKEN", "test-github-token")
os.environ.setdefault("ENABLE_SELF_HEALING_CI", "true")
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")

from app import create_app
from bigas.resources.devops.github_actions import (
    HOTFIX_BRANCH_PREFIX,
    extract_job_logs_from_zip,
    format_commit_diff,
    truncate_log_text,
)
from bigas.resources.devops.self_healing import (
    should_process_workflow_run,
    verify_github_signature,
)
from bigas.resources.devops.service import (
    create_github_pr,
    fetch_github_action_logs,
    get_commit_diff,
)


@pytest.fixture
def client():
    app = create_app()
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def _workflow_run_payload(
    *,
    action="completed",
    conclusion="failure",
    head_branch="main",
    run_id=12345,
    head_sha="abc123def456",
):
    return {
        "action": action,
        "workflow_run": {
            "id": run_id,
            "conclusion": conclusion,
            "head_branch": head_branch,
            "head_sha": head_sha,
            "html_url": f"https://github.com/acme/demo/actions/runs/{run_id}",
            "name": "CI",
        },
        "repository": {
            "name": "demo",
            "owner": {"login": "acme"},
        },
    }


class _FakeGitHubClient:
    def __init__(self, *args, **kwargs):
        self.created: dict = {}

    def list_workflow_jobs(self, owner, repo, run_id):
        return [
            {"id": 99, "name": "build", "conclusion": "failure"},
            {"id": 100, "name": "lint", "conclusion": "success"},
        ]

    def get_run_logs_zip_size(self, owner, repo, run_id):
        return 1024

    def download_run_logs_zip(self, owner, repo, run_id, *, max_bytes, dest_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("build/1_Set up job.txt", "Setting up job\n")
            zf.writestr("build/2_Run tests.txt", "##[error]Process completed with exit code 1\n")
        with open(dest_path, "wb") as handle:
            handle.write(buf.getvalue())
        return len(buf.getvalue())

    def get_job_logs(self, owner, repo, job_id):
        return "##[error]fallback job log\n"

    def get_commit(self, owner, repo, ref):
        return {
            "sha": ref,
            "commit": {
                "message": "break tests",
                "tree": {"sha": "tree-base-000"},
            },
            "files": [
                {
                    "filename": "app.py",
                    "status": "modified",
                    "patch": "@@ -1 +1 @@\n-old\n+new",
                }
            ],
        }

    def get_default_branch(self, owner, repo):
        return "main"

    def get_ref_sha(self, owner, repo, ref):
        return "base-sha-111"

    def create_blob(self, owner, repo, content):
        self.created.setdefault("blobs", []).append(content)
        return f"blob-{len(self.created['blobs'])}"

    def create_tree(self, owner, repo, base_tree_sha, entries):
        self.created["tree"] = entries
        return "tree-sha-222"

    def create_git_commit(self, owner, repo, *, message, tree_sha, parent_sha):
        self.created["commit"] = {
            "message": message,
            "tree": tree_sha,
            "parent": parent_sha,
        }
        return "commit-sha-333"

    def create_branch_ref(self, owner, repo, branch, commit_sha):
        self.created["branch"] = branch
        self.created["branch_sha"] = commit_sha

    def create_pull_request(self, owner, repo, *, title, body, head, base):
        self.created["pr"] = {
            "title": title,
            "body": body,
            "head": head,
            "base": base,
        }
        return {"number": 42, "html_url": f"https://github.com/{owner}/{repo}/pull/42"}


def test_should_process_workflow_run_filters():
    ok, reason = should_process_workflow_run(_workflow_run_payload())
    assert ok is True
    assert reason == "ok"

    ok, reason = should_process_workflow_run(_workflow_run_payload(conclusion="success"))
    assert ok is False
    assert "ignored_conclusion" in reason

    ok, reason = should_process_workflow_run(_workflow_run_payload(action="requested"))
    assert ok is False
    assert "ignored_action" in reason

    ok, reason = should_process_workflow_run(
        _workflow_run_payload(head_branch=f"{HOTFIX_BRANCH_PREFIX}run-1")
    )
    assert ok is False
    assert reason == "ignored_hotfix_branch"


def test_verify_github_signature():
    body = b'{"action":"completed"}'
    secret = "test-webhook-secret"
    import hashlib
    import hmac

    digest = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
    assert verify_github_signature(body, f"sha256={digest}", secret)
    assert not verify_github_signature(body, "sha256=deadbeef", secret)


def test_extract_job_logs_from_zip(tmp_path):
    zip_path = tmp_path / "logs.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("build/1_Setup.txt", "setup ok\n")
        zf.writestr("build/2_Test.txt", "##[error]tests failed\n")
    text = extract_job_logs_from_zip(str(zip_path), "build")
    assert "tests failed" in text


def test_truncate_log_text_tails():
    raw = "\n".join(f"line {i}" for i in range(5000))
    out = truncate_log_text(raw, tail_lines=100, max_chars=5000)
    assert "line 4999" in out
    assert "line 0" not in out


def test_format_commit_diff():
    diff = format_commit_diff(
        {
            "sha": "abc123",
            "commit": {"message": "fix: thing"},
            "files": [{"filename": "a.py", "status": "modified", "patch": "+x"}],
        }
    )
    assert "abc123" in diff
    assert "a.py" in diff


def test_fetch_github_action_logs_uses_zip(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: _FakeGitHubClient(),
    )
    result = fetch_github_action_logs(repo="acme/demo", run_id=12345)
    assert result["status"] == "ok"
    assert "build" in result["logs"]
    assert "##[error]" in result["logs"] or "tests failed" in result["logs"]


def test_get_commit_diff(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: _FakeGitHubClient(),
    )
    result = get_commit_diff(repo="acme/demo", commit_sha="abc123")
    assert result["files_changed"] == 1
    assert "app.py" in result["diff"]


def test_create_github_pr_requires_hotfix_prefix(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: _FakeGitHubClient(),
    )
    with pytest.raises(Exception) as exc:
        create_github_pr(
            repo="acme/demo",
            base_branch="main",
            new_branch_name="fix/run-1",
            title="fix",
            body="body",
            files_to_change={"README.md": "hello"},
        )
    assert "bigas-hotfix" in str(exc.value)


def test_create_github_pr_happy_path(monkeypatch):
    fake = _FakeGitHubClient()
    monkeypatch.setattr(
        "bigas.resources.devops.service._github_client",
        lambda token=None: fake,
    )
    result = create_github_pr(
        repo="acme/demo",
        base_branch="main",
        new_branch_name=f"{HOTFIX_BRANCH_PREFIX}run-99",
        title="fix(ci): tests",
        body="Automated fix",
        files_to_change={"README.md": "fixed\n"},
        base_commit_sha="parent-sha",
    )
    assert result["pr_number"] == 42
    assert fake.created["branch"] == f"{HOTFIX_BRANCH_PREFIX}run-99"
    assert fake.created["commit"]["parent"] == "parent-sha"


def _signed_headers(payload: dict) -> dict:
    body = json.dumps(payload).encode()
    import hashlib
    import hmac

    secret = os.environ["GITHUB_WEBHOOK_SECRET"]
    digest = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
    return {
        "X-GitHub-Event": "workflow_run",
        "X-Hub-Signature-256": f"sha256={digest}",
    }


def test_github_workflow_run_webhook_ignores_success(client):
    payload = _workflow_run_payload(conclusion="success")
    resp = client.post(
        "/mcp/tools/github_workflow_run",
        data=json.dumps(payload),
        content_type="application/json",
        headers=_signed_headers(payload),
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ignored") is True
    assert (data.get("postcheck") or {}).get("resumed") in (0, None)


def test_github_workflow_run_webhook_rejects_bad_signature(client):
    payload = _workflow_run_payload()
    resp = client.post(
        "/mcp/tools/github_workflow_run",
        data=json.dumps(payload),
        content_type="application/json",
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": "sha256=invalid",
        },
    )
    assert resp.status_code == 401


def test_github_workflow_run_webhook_accepts_failure(client, monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.devops.endpoints.enqueue_self_healing",
        lambda context, job_id: None,
    )
    payload = _workflow_run_payload()
    body = json.dumps(payload).encode()
    import hashlib
    import hmac

    secret = os.environ["GITHUB_WEBHOOK_SECRET"]
    digest = hmac.new(secret.encode(), msg=body, digestmod=hashlib.sha256).hexdigest()
    resp = client.post(
        "/mcp/tools/github_workflow_run",
        data=body,
        content_type="application/json",
        headers={
            "X-GitHub-Event": "workflow_run",
            "X-Hub-Signature-256": f"sha256={digest}",
        },
    )
    assert resp.status_code == 202
    data = resp.get_json()
    assert data.get("accepted") is True
    assert data.get("job_id")
    assert data.get("repo") == "acme/demo"


def test_devops_manifest_includes_new_tools(client):
    resp = client.get("/mcp/manifest")
    assert resp.status_code == 200
    tool_names = {t.get("name") for t in resp.get_json().get("tools") or []}
    assert "fetch_github_action_logs" in tool_names
    assert "create_github_pr" in tool_names
    assert "github_workflow_run" in tool_names
