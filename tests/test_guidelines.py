"""Tests mapped to Problem 01's evaluation scenarios and key technical questions.

Evaluation scenarios:
  * real backport histories from Django or CPython (their commit message formats)
  * commit messages that reference the original backported change
  * simulated dev / UAT / production repositories
Key questions:
  * identical changes whose code was modified during conflict resolution
  * how testing provenance is stored and transferred
  * what happens when a repository is cloned without the extra metadata
  * the final output stays compatible with standard git
"""

from merge_monkey.cli import main
from merge_monkey.envs import Env
from merge_monkey.gitutil import Repo
from merge_monkey.identity import find_on_branch
from merge_monkey.provenance import lookup, mark, read_note
from merge_monkey.report import ledger
from merge_monkey.resolve import resolve_worktree
from merge_monkey.trace import trace

BASE = "".join(f"value_{i} = {i}\n" for i in range(40))


def _two_branches(s, stable="stable/5.1.x"):
    s.write("models.py", BASE)
    s.commit("Initial")
    s.git("branch", stable)


# -- real-world backport formats ---------------------------------------------

def test_django_backport_note_is_traced(scratch):
    s = scratch
    _two_branches(s)
    s.write("models.py", BASE.replace("value_7 = 7", "value_7 = 70"))
    original = s.commit("Fixed #35000 -- Fixed crash in QuerySet.update().")
    s.git("checkout", "-q", "stable/5.1.x")
    s.write("models.py", BASE.replace("value_7 = 7", "value_7 = 70"))
    backport = s.commit("[5.1.x] Fixed #35000 -- Fixed crash in QuerySet.update().\n\n"
                        f"Backport of {original} from main.")
    m = find_on_branch(s.repo, original, "stable/5.1.x")
    assert (m.sha, m.method, m.confidence) == (backport, "backport note", 1.0)
    # and the other way round: from the backport back to main
    back = find_on_branch(s.repo, backport, "main")
    assert (back.sha, back.method) == (original, "original of this copy")


def test_cpython_miss_islington_backport_is_traced(scratch):
    s = scratch
    _two_branches(s, "3.13")
    s.write("models.py", BASE.replace("value_3 = 3", "value_3 = 33"))
    original = s.commit("gh-125118: Don't copy arbitrary values to _Bool (#125169)")
    s.git("checkout", "-q", "3.13")
    s.git("cherry-pick", "-x", original)
    s.git("commit", "-q", "--amend", "-m",
          "[3.13] gh-125118: Don't copy arbitrary values to _Bool (GH-125169) (#125263)\n\n"
          f"(cherry picked from commit {original})\n\nCo-authored-by: A <a@example.com>")
    backport = s.repo.must_rev("HEAD")
    m = find_on_branch(s.repo, original, "3.13")
    assert (m.sha, m.method) == (backport, "cherry-pick trailer")


def test_backport_with_short_sha_reference(scratch):
    s = scratch
    _two_branches(s, "stable")
    s.write("models.py", BASE.replace("value_9 = 9", "value_9 = 90"))
    original = s.commit("Fix value nine")
    s.git("checkout", "-q", "stable")
    s.write("models.py", BASE.replace("value_9 = 9", "value_9 = 90").replace("value_30 = 30", "value_30 = 3"))
    s.commit(f"Fix value nine\n\n(cherry picked from commit {original[:12]})")
    m = find_on_branch(s.repo, original, "stable")
    assert m.found and m.method == "cherry-pick trailer"


def test_ledger_groups_original_and_copies(demo):
    data = ledger(demo["repo"])
    hot = next(r for r in data["rows"] if "#812" in r["subject"])
    assert hot["sha"] == demo["hotfix"]
    assert hot["cells"]["prod"]["sha"] == demo["hotfix"]
    assert hot["cells"]["uat"]["sha"] == demo["hotfix_uat"]
    # every commit on every env branch appears in exactly one row
    shas = [c["sha"] for r in data["rows"] for c in r["cells"].values() if c]
    assert len(shas) == len(set(shas))


# -- simulated dev / uat / prod ------------------------------------------------

def test_demo_environments_and_expected_verdicts(demo):
    repo = demo["repo"]
    expected = {
        demo["hotfix"]: "PROVEN",
        demo["hotfix_uat"]: "PROVEN",
        demo["upload_uat"]: "TESTED",
        demo["upload"]: "TESTED",
        demo["feature"]: "UNVERIFIED",
        demo["base"]: "IN PROD, NO TEST RECORD",
    }
    for sha, verdict in expected.items():
        assert lookup(repo, sha).verdict == verdict, sha


def test_trace_reports_every_environment(demo):
    rows = {r["env"]: r for r in trace(demo["repo"], "hotfix-812")["rows"]}
    assert set(rows) == {"prod", "uat", "dev"}
    assert rows["prod"]["provenance"].verdict == "PROVEN"
    assert rows["uat"]["match"].method == "cherry-pick trailer"
    assert rows["dev"]["provenance"] is None


# -- changes modified during conflict resolution --------------------------------

def test_edited_backport_is_matched_but_needs_retest(demo):
    repo = demo["repo"]
    repo.run_raw("cherry-pick", "-x", demo["hotfix"])
    resolve_worktree(repo, "ours")          # keep dev's text: the copy differs from prod
    copy = repo.must_rev("HEAD")
    m = find_on_branch(repo, demo["hotfix"], "dev")
    assert m.sha == copy
    prov = lookup(repo, copy)
    assert prov.verdict == "NEEDS RETEST"
    assert prov.ci_source == "inherited-changed"
    assert "prod" not in prov.deployed          # different code is not "in prod"


def test_unchanged_copy_keeps_its_test_status(demo):
    prov = lookup(demo["repo"], demo["hotfix_uat"])
    assert prov.tested and prov.ci_source == "inherited-same"


def test_failure_on_identical_copy_marks_original_failing(demo):
    repo = demo["repo"]
    mark(repo, demo["hotfix_uat"], ci="failed", by="uat-ci")
    assert lookup(repo, demo["hotfix"]).verdict == "FAILING"


def test_failure_on_changed_copy_does_not_mark_original(demo):
    repo = demo["repo"]
    repo.run_raw("cherry-pick", "-x", demo["hotfix"])
    resolve_worktree(repo, "ours")
    mark(repo, "HEAD", ci="failed", by="dev-ci")
    assert lookup(repo, demo["hotfix"]).verdict == "PROVEN"


# -- storing and transferring provenance ----------------------------------------

def _publish(demo, tmp_path):
    """Push the demo repo and its notes to a bare 'remote'."""
    work = demo["repo"]
    bare = tmp_path / "remote.git"
    Repo(tmp_path).run("init", "-q", "--bare", str(bare))
    work.run("remote", "add", "origin", str(bare))
    work.run("push", "-q", "origin", "--all")
    work.run("push", "-q", "origin", "--tags")
    assert main(["-C", demo["path"], "sync"]) == 0
    return bare


def test_notes_are_plain_json_in_git(demo):
    raw = demo["repo"].run("notes", "--ref=refs/notes/merge-monkey", "show", demo["hotfix"])
    note = read_note(demo["repo"], demo["hotfix"])
    assert '"ci": "passed"' in raw
    assert note["records"][0] == {"at": "2026-10-30T09:00:00+00:00", "by": "ci-bot",
                                  "ci": "passed", "env": "prod"}


def test_provenance_travels_to_another_clone(demo, tmp_path, capsys):
    bare = _publish(demo, tmp_path)
    clone = Repo(tmp_path / "clone")
    Repo(tmp_path).run("clone", "-q", str(bare), str(tmp_path / "clone"))
    envs = [Env("prod", "origin/prod"), Env("uat", "origin/uat"), Env("dev", "origin/dev")]

    before = lookup(clone, "hotfix-812", envs)
    assert before.ci is None                       # a plain clone has no notes
    assert before.deployed["prod"] == "inferred"   # but history still says it is in prod

    assert main(["-C", str(clone.path), "setup"]) == 0
    clone.run("fetch", "-q", "origin")
    after = lookup(clone, "hotfix-812", envs)
    assert after.verdict == "PROVEN"
    assert after.deployed["prod"] == "recorded"


def test_sync_merges_records_from_both_sides(demo, tmp_path):
    bare = _publish(demo, tmp_path)
    Repo(tmp_path).run("clone", "-q", str(bare), str(tmp_path / "b"))
    other = Repo(tmp_path / "b")
    main(["-C", str(other.path), "sync"])
    mark(other, demo["feature"], ci="passed", by="teammate")
    assert main(["-C", str(other.path), "sync"]) == 0
    assert main(["-C", demo["path"], "sync"]) == 0
    note = read_note(demo["repo"], demo["feature"])
    assert note["records"][-1]["by"] == "teammate"
    assert read_note(demo["repo"], demo["hotfix"])["records"]   # own records kept


def test_notes_follow_amend_after_setup(scratch):
    s = scratch
    s.write("a.txt", "one\n")
    s.commit("one")
    mark(s.repo, "HEAD", ci="passed", by="ci")
    s.repo.run("remote", "add", "origin", str(s.path))
    assert main(["-C", str(s.path), "setup"]) == 0
    s.git("commit", "-q", "--amend", "-m", "one, reworded")
    assert read_note(s.repo, "HEAD")["records"][0]["ci"] == "passed"


# -- clone without metadata ------------------------------------------------------

def test_clone_without_notes_still_traces_through_trailers(demo, tmp_path):
    bare = tmp_path / "plain.git"
    Repo(tmp_path).run("init", "-q", "--bare", str(bare))
    demo["repo"].run("push", "-q", str(bare), "--all")     # branches only, no notes
    Repo(tmp_path).run("clone", "-q", str(bare), str(tmp_path / "c"))
    clone = Repo(tmp_path / "c")
    assert clone.run_raw("notes", "--ref=refs/notes/merge-monkey", "list").out == ""
    m = find_on_branch(clone, demo["hotfix"], "origin/uat")
    assert m.method == "cherry-pick trailer"
    envs = [Env("prod", "origin/prod")]
    prov = lookup(clone, demo["hotfix_uat"], envs)
    assert prov.deployed == {"prod": "inferred"}
    assert all(e.kind == "inferred" for e in prov.evidence)


# -- standard git compatibility ----------------------------------------------------

def test_resolved_commit_is_plain_git(demo):
    repo = demo["repo"]
    repo.run_raw("cherry-pick", "-x", demo["hotfix"])
    res = resolve_worktree(repo, "tested")
    head = repo.commit_info("HEAD")
    assert res.commit == head.sha
    assert len(head.parents) == 1                       # an ordinary cherry-pick
    assert head.author == "Meera Ops"                   # original author kept
    assert repo.run("status", "--porcelain") == ""      # clean tree, nothing staged
    assert repo.in_progress_operation() is None
    assert repo.run_raw("fsck", "--strict", "--no-dangling").code == 0
    assert repo.run_raw("grep", "-n", "^<<<<<<<", "HEAD").code == 1   # no markers anywhere

    parsed = repo.run("interpret-trailers", "--parse", input=head.message + "\n")
    keys = [line.split(":", 1)[0] for line in parsed.splitlines()]
    assert keys.count("Merge-Monkey-Theirs") == 1
    assert keys.count("Merge-Monkey-Ours") == 1
    assert keys.count("Merge-Monkey-Resolution") == 1
    # -x line is still there for tools that look for it
    assert f"(cherry picked from commit {demo['hotfix']})" in head.message


def test_resolved_merge_commit_is_plain_git(demo):
    repo = demo["repo"]
    assert repo.run_raw("merge", "--no-edit", "prod").code != 0
    resolve_worktree(repo, "tested")
    head = repo.commit_info("HEAD")
    assert head.parents == [demo["feature"], demo["hotfix"]]
    assert head.subject == "Merge branch 'prod' into dev"
    assert repo.run_raw("fsck", "--strict", "--no-dangling").code == 0
    assert repo.run("status", "--porcelain") == ""
