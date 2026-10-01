"""Prepare-staging reviews are split by file and merged back together."""
from bigas.resources.cto.pr_review.chunks import (
    POST_AUTOFIX_SLICE_INSTRUCTIONS,
    SLICE_INSTRUCTIONS,
    is_noise_path,
    merge_slice_reviews,
    review_compare_diff,
    review_slices,
    split_unified_diff,
)
from bigas.resources.devops.gpw_pipeline import StagingEnv, _launch_review_autofix


def _file(path: str, body: str = "line\n") -> str:
    return f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n-{body}+{body}"


def test_split_keeps_each_file_and_skips_lockfiles():
    diff = _file("api/order.py", "cancel\n") + _file("package-lock.json", "lock\n")
    files = split_unified_diff(diff)
    assert [path for path, _text in files] == ["api/order.py", "package-lock.json"]
    slices = review_slices(diff, max_chars=10_000)
    assert len(slices) == 1
    assert "api/order.py" in slices[0]
    assert "package-lock.json" not in slices[0]


def test_noise_paths_cover_bundles_and_built_assets():
    assert is_noise_path("package-lock.json")
    assert is_noise_path("static/js/gpw-store-bundle.8a42fa95b51b1f78b74e.js")
    assert is_noise_path("assets/app.min.js")
    assert not is_noise_path("main/views/workspace_basket.py")
    assert not is_noise_path("templates/workspace_orders.html")


def test_slices_pack_by_size_and_keep_the_tail():
    diff = "".join(_file(f"main/file_{i}.py", "x" * 30 + "\n") for i in range(5))
    slices = review_slices(diff, max_chars=120, max_slices=2)
    assert len(slices) == 2
    assert "file_0.py" in slices[0]
    assert "file_4.py" in slices[1]


def test_merge_combines_findings_and_drops_clean_slices():
    clean = """### Blockers
None.

### Important
None.

### Minor
None.
"""
    bug = """### Blockers
- Order cancel leaves invoices open.

### Important
None.

### Minor
None.
"""
    same = """### Blockers
- Order cancel leaves invoices open.

### Important
- Toast icon is white on a light background.

### Minor
None.
"""
    merged = merge_slice_reviews([clean, bug, same])
    assert merged.count("Order cancel leaves invoices open") == 1
    assert "Toast icon is white" in merged
    assert "### Important\n### Blockers" not in merged
    assert "Fix these findings before staging." in merged


def test_merge_of_clean_slices_is_ready():
    clean = """### Blockers
None.

### Important
None.

### Minor
None.
"""
    merged = merge_slice_reviews([clean, ""])
    assert "ready to merge" in merged.lower()
    assert "### Blockers\nNone." in merged


def test_review_compare_diff_calls_every_slice():
    diff = _file("api/order.py") + _file("main/views/sign_in.py") + _file("yarn.lock")
    seen = []

    def _review(slice_diff, instructions):
        seen.append((slice_diff, instructions))
        if "order.py" in slice_diff:
            return """### Blockers
- Cancel is wrong.

### Important
None.

### Minor
None.
"""
        return """### Blockers
None.

### Important
None.

### Minor
None.
"""

    merged = review_compare_diff(diff, review_slice=_review, max_chars=90)
    assert len(seen) == 2
    assert "Slice 1 of 2" in seen[0][1]
    assert "Slice 2 of 2" in seen[1][1]
    assert "do not report an undefined name" in SLICE_INSTRUCTIONS
    assert "Cancel is wrong" in merged
    assert "### Important\nNone." in merged
    assert all("yarn.lock" not in slice_diff for slice_diff, _instructions in seen)


def test_post_autofix_slices_verify_instead_of_hunting():
    diff = _file("api/order.py")

    def _review(slice_diff, instructions):
        assert "after a prepare-staging autofix merged" in instructions
        assert "Leave Important and Minor as None" in instructions
        return """### Blockers
None.

### Important
None.

### Minor
None.
"""

    merged = review_compare_diff(diff, review_slice=_review, phase="post_autofix")
    assert "ready to merge" in merged.lower()
    assert "after a prepare-staging autofix merged" in POST_AUTOFIX_SLICE_INSTRUCTIONS


def test_split_parses_paths_under_top_level_b_directory():
    diff = _file("b/order.py", "cancel\n")
    files = split_unified_diff(diff)
    assert [path for path, _text in files] == ["b/order.py"]


def test_prepare_staging_autofix_receives_the_full_review(monkeypatch):
    captured = {}

    def _launch(**kwargs):
        captured.update(kwargs)
        return {"agent_url": "https://example.test/agent"}

    monkeypatch.setattr(
        "bigas.resources.cto.deploy_hotfix.launch_failed_deploy_fix",
        _launch,
    )
    env = StagingEnv(
        project_key="GPW-PROD",
        repo="Green-Promo-Wear-Global/GPW",
        candidate_branch="develop",
        production_branch="production",
        staging_url="https://staging.example.test",
        production_url="https://example.test",
        workflows={},
    )
    body = (
        "### Blockers\n"
        + ("- A real finding that must survive the handoff.\n" * 200)
        + "\n### Important\n- Restyle the button.\n\n### Minor\n- Rename a local.\n"
    )
    assert len(body) > 4000
    launched = _launch_review_autofix(env, {"review": body})
    excerpt = captured["failures"][0]["excerpt"]
    assert len(excerpt) > 4000
    assert "must survive the handoff." in excerpt
    assert "Restyle the button" not in excerpt
    assert "Rename a local" not in excerpt
    assert "### Important\nNone." in excerpt
    assert "```python" not in excerpt or "handoff." in excerpt
    assert "Fix only the Blockers" in captured["extra_instructions"]
    assert "not a draft" in captured["extra_instructions"]
    assert "COMMITTED next to COMMITED" in captured["extra_instructions"]
    assert launched["launched"] is True
    assert launched["follows_new_pr"] is True
    assert "example.test/agent" in launched["note"]
