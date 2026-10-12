"""Nearby lines above a hunk stay visible to PR review."""

from bigas.resources.cto.pr_review.diff_context import expand_pr_diff, expand_review_diff


def _modal_file() -> str:
    pad = [f"pad-{index}" for index in range(1, 8)]
    body = [
        "type Props = {",
        "  open: boolean;",
        "  sourceWorkspaceId: string;",
        "  sourceWorkspaceName: string;",
        "  currentUserId: string;",
        "  onClose: () => void;",
    ]
    return "\n".join(pad + body) + "\n"


def _modal_diff() -> str:
    return """diff --git a/web/src/components/CreateWorkspaceModal.tsx b/web/src/components/CreateWorkspaceModal.tsx
--- a/web/src/components/CreateWorkspaceModal.tsx
+++ b/web/src/components/CreateWorkspaceModal.tsx
@@ -10,3 +10,4 @@ type Props = {
   sourceWorkspaceId: string;
   sourceWorkspaceName: string;
+  emptyPersonalIntro?: string;
   currentUserId: string;
"""


def test_expand_review_diff_includes_prop_above_the_hunk():
    expanded = expand_review_diff(
        _modal_diff(),
        lambda path: _modal_file() if path.endswith("CreateWorkspaceModal.tsx") else None,
        before=25,
    )
    assert "  open: boolean;" in expanded
    assert "type Props = {" in expanded
    assert "+  emptyPersonalIntro?: string;" in expanded
    assert "@@ -1," in expanded


def test_expand_review_diff_does_not_duplicate_lines_between_hunks():
    # Head file, after the first hunk inserted "added".
    source = "\n".join(
        ["line-1", "line-2", "line-3", "line-4", "added", "line-5"]
        + [f"line-{index}" for index in range(6, 16)]
    ) + "\n"
    diff = """diff --git a/web/src/a.ts b/web/src/a.ts
--- a/web/src/a.ts
+++ b/web/src/a.ts
@@ -4,2 +4,3 @@
 line-4
+added
 line-5
@@ -12,2 +13,2 @@
 line-12
 line-13
"""
    expanded = expand_review_diff(diff, lambda _path: source, before=25)
    assert expanded.count(" line-4\n") == 1
    assert expanded.count(" line-12\n") == 1
    assert " line-1\n" in expanded
    assert " line-11\n" in expanded


def test_expand_review_diff_leaves_new_files_unchanged():
    diff = """diff --git a/web/src/new.ts b/web/src/new.ts
new file mode 100644
--- /dev/null
+++ b/web/src/new.ts
@@ -0,0 +1,2 @@
+export const n = 1;
+
"""
    calls = []

    def read(_path):
        calls.append(_path)
        return "should-not-be-used\n"

    assert expand_review_diff(diff, read) == diff
    assert calls == []


def test_expand_pr_diff_keeps_original_when_head_lookup_fails():
    class Broken:
        def get_pull_request(self, owner, repo, pr_number):
            raise RuntimeError("github down")

    original = _modal_diff()
    assert expand_pr_diff(Broken(), "mckort", "vcfieldassistant", 425, original) == original
