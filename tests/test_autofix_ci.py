from bigas.resources.cto.autofix.ci_status import (
    annotation_excerpt,
    check_output_excerpt,
    fetch_head_ci,
    is_bigas_review_check,
    is_infrastructure_failure,
    log_excerpt,
    ready_after_ci,
)
from bigas.resources.cto.autofix.service import AutofixService


_CLEAN = (
    "### Blockers\nNone.\n\n### Important\nNone.\n\n### Minor\nNone.\n\nReady to merge.\n"
)


def test_ignores_bigas_review_jobs_and_billing_failures():
    assert is_bigas_review_check("bigas / review") is True
    assert is_bigas_review_check("test") is False
    assert is_infrastructure_failure(
        "The job was not started because your spending limit needs to be increased."
    )


def test_annotation_excerpt_skips_runner_noise():
    text = annotation_excerpt(
        [
            {"path": "", "message": "Node.js 20 is deprecated."},
            {
                "path": "backend/src/a.ts",
                "start_line": 12,
                "message": "'path' is already declared in the upper scope",
            },
        ]
    )
    assert "already declared" in text
    assert "deprecated" not in text


def test_log_excerpt_keeps_the_error_lines():
    log = "\n".join(["setup", "npm run knip", "Unused files (1)", "src/components/WaitlistForm.tsx"])
    assert "WaitlistForm" in log_excerpt(log)


def test_ready_after_ci_blocks_failed_and_optional_pending():
    failed = {"failed": [{"name": "test"}], "pending": [], "unavailable": False}
    pending = {"failed": [], "pending": ["test"], "unavailable": False}
    green = {"failed": [], "pending": [], "unavailable": False}
    assert ready_after_ci(True, failed, block_on_pending=False) == (False, False, True)
    assert ready_after_ci(True, pending, block_on_pending=False) == (True, False, False)
    assert ready_after_ci(True, pending, block_on_pending=True) == (False, True, False)
    assert ready_after_ci(True, green, block_on_pending=True) == (True, False, False)
    assert ready_after_ci(True, {"unavailable": True}, block_on_pending=True) == (
        True,
        False,
        False,
    )


class _CleanGH:
    def __init__(self, runs):
        self.runs = runs

    def get_pull_request(self, *args, **kwargs):
        return {
            "merged": False,
            "mergeable_state": "clean",
            "title": "BIG-158: Let autofix fix CI",
            "body": "",
            "head": {"ref": "feat/x"},
            "base": {"ref": "main"},
        }

    def get_pr_head_commit_meta(self, *args, **kwargs):
        return "abc123", "feat: work", "2026-09-17T17:00:00Z"

    def list_pr_commit_messages(self, *args, **kwargs):
        return []

    def get_marked_comment(self, **kwargs):
        return {"body": _CLEAN, "updated_at": "2026-09-17T17:10:00Z"}

    def list_commit_check_runs(self, *args, **kwargs):
        return self.runs

    def list_check_run_annotations(self, *args, **kwargs):
        return [
            {
                "path": "src/a.ts",
                "start_line": 3,
                "message": "'path' is already declared in the upper scope",
            }
        ]

    def count_autofix_rounds(self, *args, **kwargs):
        return 0, 0

    def record_autofix_round(self, *args, **kwargs):
        return 1, 0


def test_autofix_launches_for_failing_ci_when_review_is_clean(monkeypatch):
    launched = {}

    class FakeCursor:
        def __init__(self, api_key):
            pass

        def launch_pr_autofix(self, **kwargs):
            launched.update(kwargs)
            return {"agent_id": "bc-1", "agent_url": "https://cursor.com/agents/bc-1", "run_id": "run-1"}

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.GitHubPRCommentClient",
        lambda token: _CleanGH(
            [
                {
                    "id": 1,
                    "name": "test",
                    "status": "completed",
                    "conclusion": "failure",
                    "html_url": "https://github.com/owner/repo/actions/runs/1/job/2",
                },
                {
                    "id": 2,
                    "name": "bigas / review",
                    "status": "completed",
                    "conclusion": "failure",
                },
            ]
        ),
    )
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.CursorCloudAgentClient",
        FakeCursor,
    )
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service._ensure_issue_key_for_autofix",
        lambda **kwargs: "BIG-158",
    )
    result = AutofixService(cursor_api_key="c", github_token="t").run(
        repo="owner/repo", pr_number=9
    )
    assert result.get("launched") is True
    assert "Failing CI" in launched["prompt_text"]
    assert "already declared" in launched["prompt_text"]
    assert "bigas / review" not in launched["prompt_text"]
    assert "npm audit fix" in launched["prompt_text"]
    assert "--force" in launched["prompt_text"]


class _CheckRuns:
    def __init__(self, runs, annotations=None, log=""):
        self.runs = runs
        self.annotations = [] if annotations is None else annotations
        self.log = log

    def list_commit_check_runs(self, *args, **kwargs):
        return self.runs

    def list_check_run_annotations(self, *args, **kwargs):
        return self.annotations

    def get_actions_job_log(self, *args, **kwargs):
        return self.log


def test_fetch_head_ci_treats_timeout_cancel_and_action_required_as_failures():
    result = fetch_head_ci(
        _CheckRuns(
            [
                {"id": 1, "name": "test", "status": "completed", "conclusion": "timed_out"},
                {"id": 2, "name": "lint", "status": "completed", "conclusion": "cancelled"},
                {"id": 3, "name": "build", "status": "completed", "conclusion": "action_required"},
                {"id": 4, "name": "typecheck", "status": "completed", "conclusion": "success"},
                {"id": 5, "name": "optional", "status": "completed", "conclusion": "skipped"},
                {"id": 6, "name": "notes", "status": "completed", "conclusion": "neutral"},
            ]
        ),
        "owner",
        "repo",
        "abc",
    )
    assert [item["name"] for item in result["failed"]] == ["test", "lint", "build"]
    assert ready_after_ci(True, result, block_on_pending=False) == (False, False, True)


def test_fetch_head_ci_uses_check_output_when_annotations_and_logs_are_empty():
    summary = "AssertionError: expected 1"
    result = fetch_head_ci(
        _CheckRuns(
            [
                {
                    "id": 1,
                    "name": "test",
                    "status": "completed",
                    "conclusion": "failure",
                    "html_url": "https://github.com/owner/repo/actions/runs/1/job/9",
                    "output": {"summary": summary, "text": "full log that should not win"},
                }
            ],
            annotations=[],
            log="",
        ),
        "owner",
        "repo",
        "abc",
    )
    assert result["failed"][0]["excerpt"] == summary

    text_only = fetch_head_ci(
        _CheckRuns(
            [
                {
                    "id": 2,
                    "name": "test",
                    "status": "completed",
                    "conclusion": "timed_out",
                    "output": {"summary": "  ", "text": "job timed out after 10m"},
                }
            ]
        ),
        "owner",
        "repo",
        "abc",
    )
    assert text_only["failed"][0]["excerpt"] == "job timed out after 10m"
    long_summary = "x" * 3000
    assert check_output_excerpt({"summary": long_summary}) == "x" * 2500


def test_autofix_does_not_launch_when_ci_fails_at_max_iterations(monkeypatch):
    launched = {}

    class FakeCursor:
        def __init__(self, api_key):
            pass

        def launch_pr_autofix(self, **kwargs):
            launched.update(kwargs)
            return {
                "agent_id": "bc-next",
                "agent_url": "https://cursor.com/agents/bc-next",
                "run_id": "run-next",
            }

    class AtCap(_CleanGH):
        def count_autofix_rounds(self, *args, **kwargs):
            return 5, 0

    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.GitHubPRCommentClient",
        lambda token: AtCap(
            [
                {
                    "id": 1,
                    "name": "test",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.CursorCloudAgentClient",
        FakeCursor,
    )
    result = AutofixService(cursor_api_key="c", github_token="t").run(
        repo="owner/repo", pr_number=9
    )
    assert result["skipped"] is True
    assert result.get("loop_protection") is True
    assert result.get("launched") is not True
    assert launched == {}


def test_autofix_waits_while_ci_is_still_running(monkeypatch):
    monkeypatch.setattr(
        "bigas.resources.cto.autofix.service.GitHubPRCommentClient",
        lambda token: _CleanGH(
            [{"id": 1, "name": "test", "status": "in_progress", "conclusion": None}]
        ),
    )
    result = AutofixService(cursor_api_key="c", github_token="t").run(
        repo="owner/repo", pr_number=9
    )
    assert result["skipped"] is True
    assert result.get("checks_pending") is True
    assert result.get("cooldown") is True
    assert result.get("launched") is not True
