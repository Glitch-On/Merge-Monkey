"""Every way a conflict can stop git, and how explain/resolve handle it."""

import pytest

from merge_monkey.conflicts import analyze_worktree, parse_conflicts, worktree_conflicts
from merge_monkey.predict import predict_merge
from merge_monkey.conflicts import Analyzer
from merge_monkey.provenance import mark
from merge_monkey.resolve import choose, resolve_worktree


def _diverge(s, path="a.txt", base="x = 1\n", ours="x = 2\n", theirs="x = 3\n"):
    """main and 'other' both change the same line of `path`."""
    s.write(path, base)
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.write(path, theirs)
    s.commit("other change")
    s.git("checkout", "-q", "main")
    s.write(path, ours)
    s.commit("main change")


def _no_markers(s):
    return s.repo.run_raw("grep", "-n", "^<<<<<<<").code == 1


# -- operations ----------------------------------------------------------------

def test_merge_conflict_resolved_with_theirs(scratch):
    _diverge(scratch)
    assert scratch.repo.run_raw("merge", "other").code != 0
    res = resolve_worktree(scratch.repo, "theirs")
    assert res.complete and res.operation == "merge"
    assert (scratch.path / "a.txt").read_text() == "x = 3\n"
    assert len(scratch.repo.commit_info("HEAD").parents) == 2


def test_rebase_conflict_sides_and_continue(scratch):
    s = scratch
    _diverge(s)
    s.git("checkout", "-q", "other")
    assert s.repo.run_raw("rebase", "main").code != 0
    analysis = analyze_worktree(s.repo)
    assert analysis["operation"] == "rebase"
    hr = analysis["files"][0].hunks[0]
    # during a rebase, "ours" is the branch being rebased onto
    assert "".join(hr.hunk.ours) == "x = 2\n"
    assert "".join(hr.hunk.theirs) == "x = 3\n"
    res = resolve_worktree(s.repo, "theirs")
    assert res.complete and s.repo.in_progress_operation() is None
    assert s.repo.run("log", "-1", "--format=%s") == "other change\n"
    assert s.repo.is_ancestor("main", "HEAD")


def test_revert_conflict(scratch):
    s = scratch
    s.write("a.txt", "x = 1\n")
    s.commit("base")
    s.write("a.txt", "x = 2\n")
    target = s.commit("set two")
    s.write("a.txt", "x = 5\n")
    s.commit("set five")
    assert s.repo.run_raw("revert", "--no-edit", target).code != 0
    analysis = analyze_worktree(s.repo)
    assert analysis["operation"] == "revert"
    res = resolve_worktree(s.repo, "theirs")
    assert res.complete and res.commit and not res.empty
    assert s.repo.commit_info("HEAD").subject.startswith('Revert "set two"')
    assert (s.path / "a.txt").read_text() == "x = 1\n"


def test_resolution_that_undoes_the_pick_is_reported_empty(scratch):
    s = scratch
    s.write("a.txt", "x = 1\n")
    s.commit("base")
    s.write("a.txt", "x = 2\n")
    target = s.commit("set two")
    s.write("a.txt", "x = 5\n")
    head = s.commit("set five")
    assert s.repo.run_raw("revert", "--no-edit", target).code != 0
    res = resolve_worktree(s.repo, "ours")
    assert res.complete and res.empty and res.commit is None
    assert s.repo.must_rev("HEAD") == head
    assert s.repo.in_progress_operation() == ("revert", "REVERT_HEAD")
    assert s.repo.run_raw("revert", "--skip").code == 0


def test_cherry_pick_conflict_without_x(scratch):
    s = scratch
    _diverge(s)
    other = s.repo.must_rev("other")
    assert s.repo.run_raw("cherry-pick", other).code != 0
    res = resolve_worktree(s.repo, "theirs")
    assert res.complete
    assert "Merge-Monkey-Theirs: " + other in s.repo.commit_info("HEAD").message


def test_nothing_in_progress_is_an_error(scratch):
    scratch.write("a.txt", "x\n")
    scratch.commit("only")
    with pytest.raises(RuntimeError, match="No merge"):
        analyze_worktree(scratch.repo)


# -- conflict shapes -------------------------------------------------------------

@pytest.mark.parametrize("style", ["merge", "diff3", "zdiff3"])
def test_every_conflict_style(scratch, style):
    s = scratch
    s.git("config", "merge.conflictStyle", style)
    _diverge(s)
    assert s.repo.run_raw("merge", "other").code != 0
    hr = analyze_worktree(s.repo)["files"][0].hunks[0]
    assert "".join(hr.hunk.ours) == "x = 2\n"
    assert "".join(hr.hunk.theirs) == "x = 3\n"
    if style != "merge":
        assert "".join(hr.hunk.base) == "x = 1\n"
    assert resolve_worktree(s.repo, "ours").complete
    assert (s.path / "a.txt").read_text() == "x = 2\n"


def test_two_hunks_two_files_chosen_separately(scratch):
    s = scratch
    base = "".join(f"line{i}\n" for i in range(20))
    s.write("a.txt", base)
    s.write("b.txt", base)
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.write("a.txt", base.replace("line2\n", "A-theirs\n").replace("line15\n", "A2-theirs\n"))
    s.write("b.txt", base.replace("line5\n", "B-theirs\n"))
    s.commit("theirs")
    s.git("checkout", "-q", "main")
    s.write("a.txt", base.replace("line2\n", "A-ours\n").replace("line15\n", "A2-ours\n"))
    s.write("b.txt", base.replace("line5\n", "B-ours\n"))
    s.commit("ours")
    assert s.repo.run_raw("merge", "other").code != 0

    analysis = analyze_worktree(s.repo)
    assert sorted(f.path for f in analysis["files"]) == ["a.txt", "b.txt"]
    assert sum(len(f.hunks) for f in analysis["files"]) == 3

    picks = {("a.txt", 0): "ours", ("a.txt", 1): "theirs", ("b.txt", 0): "theirs"}

    def chooser(hr):
        return choose(hr, picks[(hr.path, hr.hunk.index)])

    res = resolve_worktree(s.repo, chooser=chooser)
    assert res.complete and len(res.decisions) == 3
    a = (s.path / "a.txt").read_text()
    assert "A-ours" in a and "A2-theirs" in a and "A-theirs" not in a
    assert "B-theirs" in (s.path / "b.txt").read_text()
    msg = s.repo.commit_info("HEAD").message
    assert msg.count("Merge-Monkey-Resolution:") == 3
    assert _no_markers(s)


def test_skipping_a_hunk_keeps_only_its_markers_and_does_not_commit(scratch):
    s = scratch
    base = "".join(f"line{i}\n" for i in range(20))
    s.write("a.txt", base)
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.write("a.txt", base.replace("line2\n", "T1\n").replace("line15\n", "T2\n"))
    s.commit("theirs")
    s.git("checkout", "-q", "main")
    s.write("a.txt", base.replace("line2\n", "O1\n").replace("line15\n", "O2\n"))
    head = s.commit("ours")
    assert s.repo.run_raw("merge", "other").code != 0

    def chooser(hr):
        return (hr.hunk.ours, "ours") if hr.hunk.index == 0 else (None, "skipped")

    res = resolve_worktree(s.repo, chooser=chooser)
    assert not res.complete and res.unresolved_files == ["a.txt"]
    assert res.commit is None
    assert s.repo.must_rev("HEAD") == head
    text = (s.path / "a.txt").read_text()
    assert "O1\n" in text and "T1" not in text
    remaining = parse_conflicts(text).hunks
    assert len(remaining) == 1 and remaining[0].ours == ["O2\n"]


def test_one_side_deleted_the_lines(scratch):
    _diverge(scratch, base="keep\nx = 1\nkeep\n", ours="keep\nx = 2\nkeep\n", theirs="keep\nkeep\n")
    assert scratch.repo.run_raw("merge", "other").code != 0
    hr = analyze_worktree(scratch.repo)["files"][0].hunks[0]
    assert hr.hunk.theirs == []
    assert [c.info.subject for c in hr.theirs.commits] == ["other change"]
    assert resolve_worktree(scratch.repo, "theirs").complete
    assert (scratch.path / "a.txt").read_text() == "keep\nkeep\n"


def test_add_add_conflict(scratch):
    s = scratch
    s.write("base.txt", "b\n")
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.write("new.txt", "from other\n")
    s.commit("add new on other")
    s.git("checkout", "-q", "main")
    s.write("new.txt", "from main\n")
    s.commit("add new on main")
    assert s.repo.run_raw("merge", "other").code != 0
    assert worktree_conflicts(s.repo) == [("new.txt", "add/add")]
    assert resolve_worktree(s.repo, "theirs").complete
    assert (s.path / "new.txt").read_text() == "from other\n"


def test_delete_modify_left_for_the_user(scratch):
    s = scratch
    s.write("a.txt", "x = 1\n")
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.write("a.txt", "x = 9\n")
    s.commit("edit")
    s.git("checkout", "-q", "main")
    s.git("rm", "-q", "a.txt")
    s.commit("delete")
    assert s.repo.run_raw("merge", "other").code != 0
    (fc,) = analyze_worktree(s.repo)["files"]
    assert fc.kind == "delete/modify"
    assert "edit" in fc.note and "theirs:" in fc.note
    res = resolve_worktree(s.repo, "tested")
    assert not res.complete and res.commit is None


def test_path_with_spaces(scratch):
    _diverge(scratch, path="my folder/the file.txt")
    assert scratch.repo.run_raw("merge", "other").code != 0
    res = resolve_worktree(scratch.repo, "ours")
    assert res.complete
    assert (scratch.path / "my folder/the file.txt").read_text() == "x = 2\n"


def test_tested_side_wins_in_a_hand_built_repo(scratch):
    _diverge(scratch)
    mark(scratch.repo, "other", env="prod", ci="passed")
    assert scratch.repo.run_raw("merge", "other").code != 0
    res = resolve_worktree(scratch.repo, "tested")
    assert res.complete
    assert (scratch.path / "a.txt").read_text() == "x = 3\n"


def test_failing_side_loses(scratch):
    _diverge(scratch)
    mark(scratch.repo, "main", ci="failed")
    assert scratch.repo.run_raw("merge", "other").code != 0
    hr = analyze_worktree(scratch.repo)["files"][0].hunks[0]
    assert hr.ours.verdict == "FAILING" and hr.preferred == "theirs"


# -- predict -------------------------------------------------------------------------

def test_predict_clean_merge(scratch):
    s = scratch
    s.write("a.txt", "a\n")
    s.write("b.txt", "b\n")
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.write("b.txt", "b2\n")
    s.commit("b")
    s.git("checkout", "-q", "main")
    s.write("a.txt", "a2\n")
    s.commit("a")
    res = predict_merge(s.repo, "other", "main", Analyzer(s.repo))
    assert res["clean"] and res["files"] == []


def test_predict_modify_delete(scratch):
    s = scratch
    s.write("a.txt", "x = 1\n")
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.git("rm", "-q", "a.txt")
    s.commit("delete")
    s.git("checkout", "-q", "main")
    s.write("a.txt", "x = 2\n")
    s.commit("edit")
    res = predict_merge(s.repo, "other", "main", Analyzer(s.repo))
    (fc,) = res["files"]
    assert not res["clean"] and fc.kind == "modify/delete" and fc.hunks == []


def test_predict_matches_the_real_merge(demo):
    """The forecast must find the same conflicts that git then really hits."""
    repo = demo["repo"]
    forecast = predict_merge(repo, "prod", "dev", Analyzer(repo))
    repo.run_raw("merge", "--no-edit", "prod")
    real = analyze_worktree(repo)
    assert [f.path for f in forecast["files"]] == [f.path for f in real["files"]]
    f_hunk, r_hunk = forecast["files"][0].hunks[0], real["files"][0].hunks[0]
    assert (f_hunk.hunk.ours, f_hunk.hunk.theirs) == (r_hunk.hunk.ours, r_hunk.hunk.theirs)
    assert f_hunk.preferred == r_hunk.preferred == "theirs"
