from bigas.resources.cto.chat_summaries import summarize_deploy_hotfix_result
from bigas.resources.cto.deploy_hotfix import build_failed_deploy_prompt
from bigas.resources.devops.github_actions import excerpt_gha_logs


def test_excerpt_gha_logs_keeps_vite_tsconfig_error():
    raw = """
deploy\tInstall\t2026-08-20T10:57:27.1411569Z 7 vulnerabilities (2 moderate, 4 high, 1 critical)
deploy\tBuild\t2026-08-20T10:57:37.6278884Z \x1b[36mvite v8.1.0 building client environment
deploy\tBuild\t2026-08-20T10:57:37.9240738Z \x1b[31m✗\x1b[39m Build failed in 295ms
deploy\tBuild\t2026-08-20T10:57:37.9243461Z error during build:
deploy\tBuild\t2026-08-20T10:57:37.9247197Z     Tsconfig not found expo/tsconfig.base
deploy\tBuild\t2026-08-20T10:57:37.9395475Z ##[error]Process completed with exit code 1.
"""
    excerpt = excerpt_gha_logs(raw)
    assert "Tsconfig not found expo/tsconfig.base" in excerpt
    assert "error during build" in excerpt.lower()
    assert "\x1b" not in excerpt


def test_excerpt_gha_logs_empty():
    assert excerpt_gha_logs("") == ""
    assert excerpt_gha_logs("   \n") == ""


def test_failed_deploy_prompt_forbids_confirmation_and_expo_install():
    prompt = build_failed_deploy_prompt(
        repo="mckort/vcfieldassistant",
        starting_ref="main",
        failures=[
            {
                "workflow": "deploy-web.yml",
                "run_id": 32361338781,
                "conclusion": "failure",
                "html_url": "https://github.com/mckort/vcfieldassistant/actions/runs/32361338781",
                "excerpt": "Tsconfig not found expo/tsconfig.base",
            }
        ],
    )
    assert "Do NOT ask for confirmation" in prompt
    assert "do not add Expo" in prompt
    assert "deploy-web.yml" in prompt
    assert "expo/tsconfig.base" in prompt
    assert "32361338781" in prompt


def test_deploy_hotfix_summary_includes_agent_link():
    text = summarize_deploy_hotfix_result(
        {
            "launched": True,
            "agent_url": "https://cursor.com/agents/bc-test",
        }
    )
    assert "Follow the agent" in text
    assert "bc-test" in text


def test_launch_failed_deploy_fix_calls_cursor(monkeypatch):
    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.delenv("DISCORD_WEBHOOK_URL_CTO", raising=False)
    called = {}

    class _FakeClient:
        def __init__(self, api_key):
            called["api_key"] = api_key

        def launch_implementation(self, **kwargs):
            called["kwargs"] = kwargs
            return {
                "agent_id": "bc-1",
                "agent_url": "https://cursor.com/agents/bc-1",
                "run_id": "r1",
            }

    monkeypatch.setattr(
        "bigas.resources.cto.deploy_hotfix.CursorCloudAgentClient",
        _FakeClient,
    )
    from bigas.resources.cto.deploy_hotfix import launch_failed_deploy_fix

    result = launch_failed_deploy_fix(
        repo="mckort/vcfieldassistant",
        failures=[
            {
                "workflow": "deploy-web.yml",
                "run_id": 1,
                "excerpt": "Tsconfig not found expo/tsconfig.base",
            }
        ],
    )
    assert result["launched"] is True
    assert result["agent_url"].endswith("bc-1")
    assert called["kwargs"]["starting_ref"] == "main"
    assert "Do NOT ask for confirmation" in called["kwargs"]["prompt_text"]
    assert "api_key" in called
