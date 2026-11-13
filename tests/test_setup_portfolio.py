"""Portfolio discovery and apply."""
from __future__ import annotations

import subprocess
from pathlib import Path

from bigas.setup_portfolio import (
    apply_projects,
    connect_argv,
    discover,
    github_repo,
    load_portfolio,
    render,
    suggest_key,
)
from bigas.setup_portfolio import PortfolioProject


def test_github_repo_parses_common_urls(monkeypatch):
    monkeypatch.setattr("bigas.setup_portfolio._ssh_hostname", lambda host: "gitlab.com" if "gitlab" in host else "")
    assert github_repo("https://github.com/acme/shop.git") == "acme/shop"
    assert github_repo("git@github.com:acme/shop.git") == "acme/shop"
    assert github_repo("ssh://git@github.com/acme/shop") == "acme/shop"
    assert github_repo("git@gitlab.com:acme/shop.git") == ""
    monkeypatch.setattr("bigas.setup_portfolio._ssh_hostname", lambda host: "github.com")
    assert github_repo("git@bigas-saas:mckort/bigas.git") == "mckort/bigas"


def test_suggest_key_keeps_short_names_and_initials():
    assert suggest_key("VFA", "vcfieldassistant", set()) == "VFA"
    assert suggest_key("GPW-PROD", "GPW", set()) == "GPW-PROD"
    assert suggest_key("bigas", "bigas", set()) == "BIGAS"
    assert suggest_key("vcfieldassistant", "vcfieldassistant", set()) == "VCFIELDA"
    assert suggest_key("green-promo-wear", "green-promo-wear", set()) == "GPW"
    used = set()
    assert suggest_key("shop", "shop", used) == "SHOP"
    assert suggest_key("Shop", "shop", used) == "SHOP2"


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True, text=True)


def _repo(path: Path, remote: str, branch: str = "main", workflows=None, origin_head: str = "") -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", branch, str(path)], check=True, capture_output=True, text=True)
    _git(path, "remote", "add", "origin", remote)
    if origin_head:
        _git(path, "symbolic-ref", "refs/remotes/origin/HEAD", f"refs/remotes/origin/{origin_head}")
    for name, body in (workflows or {}).items():
        destination = path / ".github" / "workflows" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(body, encoding="utf-8")


def test_discover_skips_non_repos_and_drafts_deploy_files(tmp_path: Path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("not a repo", encoding="utf-8")
    _repo(
        tmp_path / "shop",
        "git@github.com:acme/shop.git",
        workflows={
            "deploy.yml": "on:\n  workflow_dispatch:\n",
            "ci.yml": "on:\n  workflow_dispatch:\n",
            "test.yml": "on:\n  push:\n",
        },
    )
    _repo(tmp_path / "learn", "https://github.com/acme/learn.git", branch="master", origin_head="master")
    bare = tmp_path / "local-only"
    bare.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(bare)], check=True, capture_output=True, text=True)
    _repo(tmp_path / "mirror", "git@gitlab.com:acme/mirror.git")

    result = discover(tmp_path)
    by_path = {project.path: project for project in result.projects}
    assert set(by_path) == {"shop", "learn"}
    assert by_path["shop"].repo == "acme/shop"
    assert by_path["shop"].deploy == ["deploy.yml"]
    assert by_path["shop"].branch == "main"
    assert any("ci.yml" in note for note in by_path["shop"].notes)
    assert any("local checkout" in note for note in by_path["shop"].notes)
    assert by_path["learn"].branch == "master"
    assert not any("local checkout" in note for note in by_path["learn"].notes)
    assert any("shop (deploy.yml)" in note for note in by_path["learn"].notes)
    text = "\n".join(result.preamble)
    assert "notes" in text
    assert "local-only" in text
    assert "mirror" in text

    loaded = load_portfolio(render(result))
    assert [(project.key, project.repo, project.deploy) for project in loaded] == [
        (by_path[name].key, by_path[name].repo, by_path[name].deploy) for name in sorted(by_path, key=str.lower)
    ]


def test_discover_keeps_one_checkout_per_github_repo(tmp_path: Path):
    shop = tmp_path / "shop"
    _repo(shop, "git@github.com:acme/shop.git", workflows={"deploy.yml": "on:\n  workflow_dispatch:\n"})
    _git(shop, "config", "user.email", "t@example.com")
    _git(shop, "config", "user.name", "Test")
    _git(shop, "commit", "--allow-empty", "-m", "init")
    subprocess.run(
        ["git", "-C", str(shop), "worktree", "add", "-b", "feature", str(tmp_path / "shop-feature")],
        check=True,
        capture_output=True,
        text=True,
    )
    result = discover(tmp_path)
    assert [project.path for project in result.projects] == ["shop"]
    assert any("shop-feature" in note for note in result.projects[0].notes)


def test_discover_assigns_keys_after_dropping_extra_checkouts(tmp_path: Path):
    _repo(tmp_path / "newsletter", "git@github.com:acme/remotebrief.git")
    _repo(tmp_path / "remotebrief", "git@github.com:acme/remotebrief.git")
    result = discover(tmp_path)
    assert [project.path for project in result.projects] == ["remotebrief"]
    assert result.projects[0].key == "REMOTEBR"


def test_roundtrip_keeps_deploy_repo_and_release():
    project = PortfolioProject(
        key="SITE",
        repo="acme/marketing",
        branch="main",
        name="Acme Marketing",
        path="marketing",
        deploy=["deploy.yml"],
        deploy_repo="acme/infra",
        release="staging",
    )
    from bigas.setup_portfolio import DiscoverResult

    loaded = load_portfolio(render(DiscoverResult([project], [], "/tmp/Code")))
    assert loaded[0].deploy_repo == "acme/infra"
    assert loaded[0].release == "staging"
    argv = connect_argv(loaded[0], apply=True, webhook=True, skip_github=True, env_file=".env")
    assert argv == [
        "--key", "SITE",
        "--repo", "acme/marketing",
        "--branch", "main",
        "--name", "Acme Marketing",
        "--deploy", "deploy.yml",
        "--deploy-repo", "acme/infra",
        "--versioned", "staging",
        "--env-file", ".env",
        "--webhook",
        "--skip-github",
        "--apply",
    ]


def test_example_portfolio_loads():
    text = Path("docs/portfolio.example.yaml").read_text(encoding="utf-8")
    projects = load_portfolio(text)
    assert [project.key for project in projects] == ["ACME", "AURA", "SITE", "VFA"]
    assert projects[2].deploy_repo == "acme/infra"
    assert projects[3].release == "staging"
    assert projects[1].branch == "master"


def test_apply_projects_plans_then_stops_on_failure():
    projects = [
        PortfolioProject(key="ACME", repo="acme/shop", deploy=["deploy.yml"]),
        PortfolioProject(key="BIG", repo="acme/bigas"),
    ]
    calls = []

    def connect(argv):
        calls.append(argv)
        return 0 if len(calls) == 1 else 7

    assert apply_projects(projects, apply=False, webhook=False, skip_github=True, env_file="", connect=connect) == 7
    assert calls[0][:4] == ["--key", "ACME", "--repo", "acme/shop"]
    assert "--apply" not in calls[0]
    assert "--skip-github" in calls[0]
    assert calls[1][1] == "BIG"


def test_apply_projects_rejects_a_bad_key_before_connecting():
    calls = []
    code = apply_projects(
        [PortfolioProject(key="123", repo="acme/shop")],
        apply=True,
        webhook=False,
        skip_github=False,
        env_file="",
        connect=lambda argv: calls.append(argv) or 0,
    )
    assert code == 2
    assert calls == []


def test_apply_projects_rejects_duplicate_keys_and_repos(capsys):
    calls = []

    def connect(argv):
        calls.append(argv)
        return 0

    code = apply_projects(
        [
            PortfolioProject(key="ACME", repo="acme/shop"),
            PortfolioProject(key="acme", repo="acme/other"),
        ],
        apply=True,
        webhook=False,
        skip_github=True,
        env_file="",
        connect=connect,
    )
    assert code == 2
    assert calls == []
    assert "duplicate key ACME" in capsys.readouterr().out

    code = apply_projects(
        [
            PortfolioProject(key="ACME", repo="acme/shop"),
            PortfolioProject(key="BIG", repo="acme/shop"),
        ],
        apply=True,
        webhook=False,
        skip_github=True,
        env_file="",
        connect=connect,
    )
    assert code == 2
    assert calls == []
    assert "duplicate repo acme/shop" in capsys.readouterr().out


def test_ssh_hostname_does_not_treat_a_hyphen_host_as_a_flag(monkeypatch):
    import bigas.setup_portfolio as portfolio

    monkeypatch.setattr(portfolio, "_HOST_CACHE", {})
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        return subprocess.CompletedProcess(args, 0, "hostname github.com\n", "")

    monkeypatch.setattr(portfolio.subprocess, "run", fake_run)
    assert portfolio._ssh_hostname("-weird-alias") == "github.com"
    assert captured["args"] == ["ssh", "-G", "--", "-weird-alias"]
