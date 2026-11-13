"""Portfolio file: one row per board project, drafted from a folder of clones.

A recommended layout is one parent directory whose subfolders are git clones
(``~/Code/bigas``, ``~/Code/shop``, …). ``discover`` reads each clone's GitHub
remote, the default branch when GitHub has recorded it, and any
``workflow_dispatch`` file whose name contains ``deploy``. A second checkout
of the same repo, including a linked worktree, is skipped.

The written file is ``bigas.portfolio.yaml``. It is gitignored. Show it to the
human before ``apply``: keys are a guess, and a deploy workflow that lives in
another repo is only mentioned, never filled in.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

import yaml

from bigas.setup_repo import RepoSpec, _KEY_RE

# scp-style (git@host:owner/repo) and URL-style. The host may be an SSH alias.
_SCP_REMOTE = re.compile(
    r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)
_URL_REMOTE = re.compile(
    r"^(?:https?|ssh)://(?:[^@/]+@)?(?P<host>[^/]+)/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_HOST_CACHE: dict = {}


@dataclass
class PortfolioProject:
    key: str
    repo: str
    branch: str = "main"
    name: str = ""
    path: str = ""
    deploy: List[str] = field(default_factory=list)
    deploy_repo: str = ""
    release: str = ""
    notes: List[str] = field(default_factory=list)
    worktree: bool = False

    def to_spec(self) -> RepoSpec:
        return RepoSpec(
            key=(self.key or "").strip().upper(),
            repo=(self.repo or "").strip().strip("/"),
            branch=(self.branch or "main").strip() or "main",
            name=(self.name or "").strip(),
            deploy_workflows=[w.strip() for w in self.deploy if w and str(w).strip()],
            deploy_repo=(self.deploy_repo or "").strip(),
            release_prefix=(self.release or "").strip(),
        )


@dataclass
class DiscoverResult:
    projects: List[PortfolioProject]
    preamble: List[str]
    root: str


def _ssh_hostname(host: str) -> str:
    """Hostname from ``ssh -G``, so an alias such as ``bigas-saas`` can mean github.com."""
    key = (host or "").lower()
    if key in _HOST_CACHE:
        return _HOST_CACHE[key]
    name = ""
    try:
        proc = subprocess.run(["ssh", "-G", host], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    if proc is not None and proc.returncode == 0:
        for line in (proc.stdout or "").splitlines():
            if line.lower().startswith("hostname "):
                name = line.split(None, 1)[1].strip().lower()
                break
    _HOST_CACHE[key] = name
    return name


def _is_github(host: str) -> bool:
    host = (host or "").lower()
    if host == "github.com" or host.endswith(".github.com"):
        return True
    resolved = _ssh_hostname(host)
    return resolved == "github.com" or resolved.endswith(".github.com")


def github_repo(remote_url: str) -> str:
    """``owner/repo`` when the remote is GitHub, including an SSH host alias.

    ``git@github.com:acme/shop.git`` and ``git@bigas-saas:acme/shop.git`` both
    count when ``ssh -G bigas-saas`` reports ``hostname github.com``. Anything
    else, including GitLab, returns ``""``.
    """
    remote = (remote_url or "").strip()
    match = _URL_REMOTE.match(remote) or _SCP_REMOTE.match(remote)
    if not match or not _is_github(match.group("host")):
        return ""
    return f"{match.group('owner')}/{match.group('repo')}"


def suggest_key(directory: str, repo_name: str, used: set) -> str:
    """A board key the human is expected to shorten.

    An already-short folder name (``VFA``, ``GPW-PROD``, ``bigas``) is kept.
    A hyphenated repo becomes initials (``green-promo-wear`` → ``GPW``).
    Anything else is the repo name, uppercased, cut to 8 characters.
    """
    folder = (directory or "").strip()
    upper_folder = folder.upper()
    short_folder = (
        _KEY_RE.match(upper_folder)
        and len(upper_folder) <= 10
        and (folder == upper_folder or "-" in folder or len(upper_folder) <= 6)
    )
    if short_folder:
        candidate = upper_folder
    else:
        parts = [part for part in re.split(r"[-_]+", repo_name or "") if part]
        initials = "".join(part[0] for part in parts if part[:1].isalpha()).upper()
        if len(parts) >= 2 and 2 <= len(initials) <= 8:
            candidate = initials
        else:
            letters = re.sub(r"[^A-Za-z0-9]", "", repo_name or "").upper()
            candidate = (letters[:8] or "APP")
    if not _KEY_RE.match(candidate):
        candidate = "APP"
    base = candidate
    n = 2
    while candidate in used:
        candidate = f"{base[:8]}{n}"
        n += 1
    used.add(candidate)
    return candidate


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return ""
    return (proc.stdout or "").strip()


def _branch(repo: Path) -> Tuple[str, bool]:
    """Return ``(branch, guessed)``. Guessed means origin/HEAD was not set."""
    ref = _git(repo, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    prefix = "refs/remotes/origin/"
    if ref.startswith(prefix) and ref[len(prefix):]:
        return ref[len(prefix):], False
    head = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if head and head != "HEAD":
        return head, True
    return "main", True


def _is_linked_worktree(repo: Path) -> bool:
    """A linked worktree has a ``.git`` file. The main clone has a ``.git`` directory."""
    return (repo / ".git").is_file()


def _prefer(candidate: PortfolioProject, current: PortfolioProject) -> bool:
    """True when ``candidate`` is the checkout to keep for this GitHub repo."""
    if current.worktree and not candidate.worktree:
        return True
    if candidate.worktree and not current.worktree:
        return False
    repo_name = candidate.repo.split("/", 1)[1].lower()
    candidate_match = candidate.path.lower() == repo_name
    current_match = current.path.lower() == repo_name
    if candidate_match and not current_match:
        return True
    if current_match and not candidate_match:
        return False
    return len(candidate.path) < len(current.path)


def _one_per_repo(projects: List[PortfolioProject]) -> Tuple[List[PortfolioProject], List[str]]:
    """Keep one checkout per GitHub repo. Extra worktrees become a comment."""
    chosen: dict = {}
    skipped: dict = {}
    for project in projects:
        current = chosen.get(project.repo)
        if current is None:
            chosen[project.repo] = project
            continue
        if _prefer(project, current):
            skipped.setdefault(project.repo, []).append(current.path)
            chosen[project.repo] = project
        else:
            skipped.setdefault(project.repo, []).append(project.path)
    ordered = [project for project in projects if chosen.get(project.repo) is project]
    notes = []
    for repo, paths in skipped.items():
        kept = chosen[repo]
        extra = ", ".join(paths)
        kept.notes.append(f"also checked out at {extra}. Those folders are not separate projects.")
        notes.append(f"# Extra checkout of {repo}, left out: {extra}")
    return ordered, notes


def _workflows(repo: Path) -> Tuple[List[str], List[str]]:
    """``(deploy files, other workflow_dispatch files)``."""
    directory = repo / ".github" / "workflows"
    if not directory.is_dir():
        return [], []
    deploy, others = [], []
    paths = sorted(directory.glob("*.yml")) + sorted(directory.glob("*.yaml"))
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if "workflow_dispatch" not in text:
            continue
        if "deploy" in path.name.lower():
            deploy.append(path.name)
        else:
            others.append(path.name)
    return deploy, others


def discover(root: Path) -> DiscoverResult:
    """Scan one level of ``root``. Non-git folders and non-GitHub remotes are skipped."""
    root = root.expanduser().resolve()
    skipped_git: List[str] = []
    skipped_origin: List[str] = []
    skipped_github: List[str] = []
    projects: List[PortfolioProject] = []
    used: set = set()
    if not root.is_dir():
        return DiscoverResult([], [f"# {root} is not a directory"], str(root))

    children = sorted(
        (path for path in root.iterdir() if path.is_dir() and not path.name.startswith(".")),
        key=lambda path: path.name.lower(),
    )
    for child in children:
        if not (child / ".git").exists():
            skipped_git.append(child.name)
            continue
        remote = _git(child, "remote", "get-url", "origin")
        if not remote:
            skipped_origin.append(child.name)
            continue
        repo = github_repo(remote)
        if not repo:
            skipped_github.append(child.name)
            continue
        branch, guessed = _branch(child)
        deploy, others = _workflows(child)
        notes: List[str] = []
        if guessed:
            notes.append(
                f"branch is the local checkout. Confirm: gh repo view {repo} --json defaultBranchRef"
            )
        if others:
            notes.append("workflow_dispatch files not added as deploy: " + ", ".join(others))
        if not deploy:
            notes.append("no deploy workflow in this repo.")
        projects.append(
            PortfolioProject(
                key="",
                repo=repo,
                branch=branch,
                name=re.sub(r"[-_]+", " ", child.name).strip(),
                path=child.name,
                deploy=deploy,
                notes=notes,
                worktree=_is_linked_worktree(child),
            )
        )

    projects, extra_notes = _one_per_repo(projects)
    # Keys are assigned after extra checkouts are dropped, so a worktree
    # that sorts first does not take the short name.
    for project in projects:
        repo_name = project.repo.split("/", 1)[1]
        project.key = suggest_key(project.path, repo_name, used)

    deployers = [project for project in projects if project.deploy]
    if deployers:
        summary = ", ".join(f"{project.path} ({', '.join(project.deploy)})" for project in deployers)
        for project in projects:
            if not project.deploy:
                project.notes.append(
                    "other clones with a deploy workflow: "
                    + summary
                    + ". Set deploy_repo and deploy if this project ships from one of them."
                )

    preamble: List[str] = list(extra_notes)
    if skipped_git:
        preamble.append("# Ignored (not a git repo): " + ", ".join(skipped_git))
    if skipped_origin:
        preamble.append("# Ignored (no origin remote): " + ", ".join(skipped_origin))
    if skipped_github:
        preamble.append("# Ignored (origin is not GitHub): " + ", ".join(skipped_github))
    return DiscoverResult(projects, preamble, str(root))


def _quote(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_./@:-]+", value or ""):
        return value
    return json.dumps(value)


def render(result: DiscoverResult) -> str:
    lines = [
        "# Drafted by scripts/discover_portfolio.py from " + result.root,
        "# Delete any project you do not want on the board.",
        "# Edit key before apply. Cards are named KEY-1, KEY-2.",
        "# branch: GitHub default branch. A comment marks a local guess.",
        "# deploy: workflow_dispatch files whose name contains 'deploy'.",
        "# deploy_repo: set when those files live in a different repo.",
        "# release: set to staging for versioned releases (staging-x.y.z). Omit for development.",
        "",
    ]
    lines.extend(result.preamble)
    if result.preamble:
        lines.append("")
    lines.append("projects:")
    if not result.projects:
        lines.append("  []")
    for project in result.projects:
        for note in project.notes:
            lines.append(f"  # {note}")
        lines.append(f"  - key: {project.key}")
        lines.append(f"    repo: {project.repo}")
        lines.append(f"    branch: {project.branch}")
        if project.name:
            lines.append(f"    name: {_quote(project.name)}")
        if project.path:
            lines.append(f"    path: {_quote(project.path)}")
        deploy = ", ".join(project.deploy)
        lines.append(f"    deploy: [{deploy}]")
        if project.deploy_repo:
            lines.append(f"    deploy_repo: {project.deploy_repo}")
        if project.release:
            lines.append(f"    release: {project.release}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _as_list(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    return [str(part).strip() for part in value if str(part).strip()]


def load_portfolio(text: str) -> List[PortfolioProject]:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("Portfolio file must be a mapping with a projects list.")
    rows = data.get("projects") or []
    if not isinstance(rows, list):
        raise ValueError("projects must be a list.")
    projects: List[PortfolioProject] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"Each project must be a mapping, got {row!r}")
        projects.append(
            PortfolioProject(
                key=str(row.get("key") or ""),
                repo=str(row.get("repo") or ""),
                branch=str(row.get("branch") or "main"),
                name=str(row.get("name") or ""),
                path=str(row.get("path") or ""),
                deploy=_as_list(row.get("deploy")),
                deploy_repo=str(row.get("deploy_repo") or ""),
                release=str(row.get("release") or ""),
            )
        )
    return projects


def connect_argv(
    project: PortfolioProject,
    *,
    apply: bool = False,
    webhook: bool = False,
    skip_github: bool = False,
    env_file: str = "",
    bigas_url: str = "",
) -> List[str]:
    """Arguments for ``scripts/connect_repo.py`` for one portfolio row."""
    spec = project.to_spec()
    argv = ["--key", spec.key, "--repo", spec.repo, "--branch", spec.branch]
    if spec.name:
        argv += ["--name", spec.name]
    if spec.deploy_workflows:
        argv += ["--deploy", ",".join(spec.deploy_workflows)]
    if spec.deploy_repo:
        argv += ["--deploy-repo", spec.deploy_repo]
    if spec.release_prefix:
        argv += ["--versioned", spec.release_prefix]
    if env_file:
        argv += ["--env-file", env_file]
    if bigas_url:
        argv += ["--bigas-url", bigas_url]
    if webhook:
        argv.append("--webhook")
    if skip_github:
        argv.append("--skip-github")
    if apply:
        argv.append("--apply")
    return argv


def apply_projects(
    projects: Sequence[PortfolioProject],
    *,
    apply: bool,
    webhook: bool,
    skip_github: bool,
    env_file: str,
    connect: Callable[[List[str]], int],
    bigas_url: str = "",
) -> int:
    """Validate every row, then plan or apply each one. Stops at the first failure."""
    if not projects:
        print("✗ No projects in the portfolio. Delete is fine; an empty file connects nothing.")
        return 2
    planned: List[Tuple[PortfolioProject, List[str]]] = []
    for project in projects:
        spec = project.to_spec()
        errors = spec.validate()
        if errors:
            for error in errors:
                label = project.path or project.repo or project.key
                print(f"✗ {label}: {error}")
            return 2
        planned.append(
            (
                project,
                connect_argv(
                    project,
                    apply=apply,
                    webhook=webhook,
                    skip_github=skip_github,
                    env_file=env_file,
                    bigas_url=bigas_url,
                ),
            )
        )
    for _project, argv in planned:
        code = connect(argv)
        if code:
            return code
    return 0
