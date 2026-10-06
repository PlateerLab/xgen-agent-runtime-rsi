"""후보 초안 공간 — git 없이 후보마다 하네스 사본을 두고, 검토자가 읽는 diff 를 git 모양으로 만든다."""

from __future__ import annotations

from xgen_rsi.consolidate.drafts import DraftSpace, diff_trees
from xgen_rsi.evolve.critic import added_text


def _tree(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def test_drafts_are_independent_copies(tmp_path):
    inc = _tree(tmp_path / "inc", {"manifest.json": "{}\n", "skills/a/SKILL.md": "a\n"})
    space = DraftSpace(tmp_path / "space", inc)
    a, b = space.new_draft("A"), space.new_draft("B")
    (a / "skills/a/SKILL.md").write_text("changed\n", encoding="utf-8")
    assert (b / "skills/a/SKILL.md").read_text(encoding="utf-8") == "a\n"
    assert space.diff(b) == ""


def test_diff_shows_modified_new_and_deleted_files(tmp_path):
    a = _tree(tmp_path / "a", {"manifest.json": "{\n  \"x\": 1\n}\n", "old.md": "gone\n"})
    b = _tree(tmp_path / "b", {"manifest.json": "{\n  \"x\": 2\n}\n", "skills/new/SKILL.md": "Use a table.\n"})
    diff = diff_trees(a, b, prefix="harness")
    assert "diff --git a/harness/manifest.json b/harness/manifest.json" in diff
    assert "+  \"x\": 2" in diff and "-  \"x\": 1" in diff
    assert "new file mode 100644\n--- /dev/null\n+++ b/harness/skills/new/SKILL.md" in diff
    assert "deleted file mode 100644\n--- a/harness/old.md\n+++ /dev/null" in diff
    # 검토자의 결정적 사전 검사가 읽는 것: 새 경로와 + 줄
    added = added_text(diff)
    assert "harness/skills/new/SKILL.md" in added and "Use a table." in added and "gone" not in added
