"""End to end on the demo repository: predict, conflict, explain, resolve."""

from merge_monkey.conflicts import Analyzer, analyze_worktree
from merge_monkey.predict import predict_merge, predict_picks
from merge_monkey.provenance import lookup, read_note
from merge_monkey.report import ledger, render_html
from merge_monkey.resolve import resolve_worktree
from merge_monkey.trace import trace


def _pick_hotfix(demo):
    res = demo["repo"].run_raw("cherry-pick", "-x", demo["hotfix"])
    assert res.code != 0, "the demo backport is supposed to conflict"


def test_trace_follows_hotfix(demo):
    result = trace(demo["repo"], "hotfix-812")
    rows = {r["branch"]: r for r in result["rows"]}
    assert rows["prod"]["match"].method == "same commit"
    assert rows["uat"]["match"].sha == demo["hotfix_uat"]
    assert not rows["dev"]["match"].found


def test_predict_merge_labels_sides(demo):
    res = predict_merge(demo["repo"], "prod", "dev", Analyzer(demo["repo"]))
    assert not res["clean"]
    (fc,) = res["files"]
    assert fc.path == "settings.py"
    (hr,) = fc.hunks
    assert [c.info.sha for c in hr.theirs.commits] == [demo["hotfix"]]
    assert [c.info.sha for c in hr.ours.commits] == [demo["feature"]]
    assert hr.preferred == "theirs"


def test_predict_picks(demo):
    res = predict_picks(demo["repo"], "prod", "dev", Analyzer(demo["repo"]))
    (pick,) = res["picks"]
    assert pick["sha"] == demo["hotfix"] and pick["status"] == "conflict"
    # uat -> prod: the upload change is missing from prod and applies cleanly
    res = predict_picks(demo["repo"], "uat", "prod", Analyzer(demo["repo"]))
    assert {p["status"] for p in res["picks"]} == {"clean"}


def test_predict_does_not_touch_worktree(demo):
    repo = demo["repo"]
    before = repo.run("status", "--porcelain")
    predict_merge(repo, "prod", "dev", Analyzer(repo))
    assert repo.run("status", "--porcelain") == before
    assert repo.in_progress_operation() is None


def test_explain_during_cherry_pick(demo):
    _pick_hotfix(demo)
    analysis = analyze_worktree(demo["repo"])
    assert analysis["operation"] == "cherry-pick"
    (fc,) = analysis["files"]
    (hr,) = fc.hunks
    assert hr.theirs.verdict == "PROVEN"
    assert hr.ours.verdict == "UNVERIFIED"
    assert "SESSION_TIMEOUT = 1800" in "".join(hr.hunk.theirs)


def test_resolve_prefers_tested_and_commits(demo):
    repo = demo["repo"]
    _pick_hotfix(demo)
    res = resolve_worktree(repo, "tested")
    assert res.complete and res.commit
    assert repo.in_progress_operation() is None
    settings = (repo.root / "settings.py").read_text()
    assert "<<<<<<<" not in settings and "SESSION_TIMEOUT = 1800" in settings
    msg = repo.commit_info("HEAD").message
    assert f"(cherry picked from commit {demo['hotfix']})" in msg
    assert f"Merge-Monkey-Theirs: {demo['hotfix']} PROVEN" in msg
    assert f"Merge-Monkey-Ours: {demo['feature']} UNVERIFIED" in msg
    assert "Merge-Monkey-Resolution: settings.py hunk 1 -> theirs" in msg
    # plain git can still read the trailers
    trailers = repo.run("log", "-1", "--format=%(trailers:key=Merge-Monkey-Theirs,valueonly)")
    assert demo["hotfix"] in trailers
    note = read_note(repo, res.commit)
    assert note["status"].startswith("unverified")
    assert lookup(repo, "HEAD").verdict == "NEEDS RETEST"


def test_resolve_ours_and_no_commit(demo):
    repo = demo["repo"]
    _pick_hotfix(demo)
    res = resolve_worktree(repo, "ours", commit=False)
    assert res.complete and res.commit is None
    assert repo.in_progress_operation() is not None
    assert "SESSION_TIMEOUT = 7200" in (repo.root / "settings.py").read_text()


def test_resolve_tie_leaves_markers(scratch):
    s = scratch
    s.write("a.txt", "x = 1\n")
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.write("a.txt", "x = 2\n")
    s.commit("other change")
    s.git("checkout", "-q", "main")
    s.write("a.txt", "x = 3\n")
    s.commit("main change")
    assert s.repo.run_raw("merge", "other").code != 0
    res = resolve_worktree(s.repo, "tested")
    assert not res.complete
    assert "<<<<<<<" in (s.path / "a.txt").read_text()


def test_modify_delete_is_reported(scratch):
    s = scratch
    s.write("a.txt", "x = 1\n")
    s.commit("base")
    s.git("checkout", "-q", "-b", "other")
    s.git("rm", "-q", "a.txt")
    s.commit("delete a")
    s.git("checkout", "-q", "main")
    s.write("a.txt", "x = 3\n")
    s.commit("edit a")
    assert s.repo.run_raw("merge", "other").code != 0
    analysis = analyze_worktree(s.repo)
    (fc,) = analysis["files"]
    assert fc.kind == "modify/delete" and fc.hunks == []
    res = resolve_worktree(s.repo, "tested")
    assert res.unresolved_files == ["a.txt"]


def test_merge_conflict_resolution(demo):
    repo = demo["repo"]
    assert repo.run_raw("merge", "--no-edit", "prod").code != 0
    res = resolve_worktree(repo, "tested")
    assert res.complete and res.operation == "merge"
    assert len(repo.commit_info("HEAD").parents) == 2


def test_report(demo):
    data = ledger(demo["repo"])
    by_subject = {r["subject"]: r for r in data["rows"]}
    hot = by_subject["Fix crash on long sessions (#812)"]
    assert hot["cells"]["dev"] is None
    assert hot["cells"]["prod"]["verdict"] == "PROVEN"
    assert "<table>" in render_html(data)
