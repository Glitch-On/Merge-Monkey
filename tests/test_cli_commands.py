"""Every command, in text and JSON form, with its exit code."""

import builtins
import json
import subprocess
import sys

import pytest

from merge_monkey.cli import main
from merge_monkey.gitutil import Repo
from merge_monkey.provenance import NOTES_REF


def run(capsys, *args):
    code = main([str(a) for a in args])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def d(tmp_path, capsys):
    path = tmp_path / "d"
    assert run(capsys, "demo", path)[0] == 0
    return path


def picked(path):
    Repo(path).run_raw("cherry-pick", "-x", "hotfix-812")
    return path


# -- mark / show ------------------------------------------------------------------

def test_mark_text_and_json(d, capsys):
    code, out, _ = run(capsys, "-C", d, "mark", "dev", "--env", "uat", "--ci", "passed", "--by", "qa",
                       "--note", "smoke ok")
    assert code == 0 and "✓ Recorded on" in out and "env=uat" in out and "note=smoke ok" in out
    code, out, _ = run(capsys, "-C", d, "mark", "dev", "--tests", "failed", "--json")
    rec = json.loads(out)["record"]
    assert rec["tests"] == "failed" and "at" in rec


def test_mark_needs_something_to_record(d, capsys):
    code, _, err = run(capsys, "-C", d, "mark", "dev")
    assert code == 2 and "nothing to record" in err


def test_mark_rejects_bad_result(d, capsys):
    with pytest.raises(SystemExit):
        main(["-C", str(d), "mark", "dev", "--ci", "maybe"])
    assert "invalid choice" in capsys.readouterr().err


def test_show_text_lists_evidence(d, capsys):
    code, out, _ = run(capsys, "-C", d, "show", "hotfix-812")
    assert code == 0
    assert "PROVEN" in out and "recorded: deployed to prod" in out and "inferred:" in out


def test_show_defaults_to_head(d, capsys):
    code, out, _ = run(capsys, "-C", d, "show", "--json")
    assert json.loads(out)["sha"] == Repo(d).must_rev("HEAD")


# -- trace ----------------------------------------------------------------------------

def test_trace_text(d, capsys):
    code, out, _ = run(capsys, "-C", d, "trace", "hotfix-812", "--no-ai")
    assert code == 0
    lines = out.splitlines()
    assert any(l.strip().startswith("prod") and "same commit" in l for l in lines)
    assert any(l.strip().startswith("uat") and "cherry-pick trailer" in l for l in lines)
    assert any(l.strip().startswith("dev") and "MISSING" in l for l in lines)


def test_trace_explicit_branches(d, capsys):
    code, out, _ = run(capsys, "-C", d, "trace", "hotfix-812", "--branches", "main,uat", "--json", "--no-ai")
    rows = {b["branch"]: b for b in json.loads(out)["branches"]}
    assert set(rows) == {"main", "uat"}
    assert rows["main"]["match"]["sha"] is None
    assert rows["uat"]["env"] == "uat"


def test_trace_unknown_commit(d, capsys):
    code, _, err = run(capsys, "-C", d, "trace", "does-not-exist", "--no-ai")
    assert code == 2 and "unknown revision" in err


# -- predict ------------------------------------------------------------------------

def test_predict_pick_text_and_verbose(d, capsys):
    code, out, _ = run(capsys, "-C", d, "predict", "--pick", "prod", "dev", "-v")
    assert code == 0
    assert "CONFLICT" in out and "settings.py (content, 1 hunk(s))" in out
    assert "Provenance says: Prefer THEIRS" in out          # -v prints the hunk


def test_predict_pick_lists_present_clean_and_conflict(d, capsys):
    # backport the hotfix to dev keeping dev's text: an edited copy
    picked(d)
    from merge_monkey.resolve import resolve_worktree
    resolve_worktree(Repo(d), "ours")
    code, out, _ = run(capsys, "-C", d, "predict", "--pick", "dev", "prod")
    assert code == 0
    lines = {l.split()[2]: l.split()[0] for l in out.splitlines() if l.startswith("  ") and len(l.split()) > 2
             and l.split()[0] in ("clean", "CONFLICT", "present")}
    assert sorted(lines.values()) == ["CONFLICT", "clean", "present"]
    assert "original of this copy" in out


def test_predict_pick_json_and_limit(d, capsys):
    code, out, _ = run(capsys, "-C", d, "predict", "--pick", "dev", "prod", "--json", "--limit", "1")
    data = json.loads(out)
    assert data["mode"] == "pick" and data["truncated"] is True and len(data["picks"]) == 1
    code, out, _ = run(capsys, "-C", d, "predict", "--pick", "dev", "prod", "--json")
    statuses = sorted(p["status"] for p in json.loads(out)["picks"])
    assert statuses == ["clean", "conflict"]


def test_predict_pick_nothing_missing(d, capsys):
    code, out, _ = run(capsys, "-C", d, "predict", "--pick", "main", "prod")
    assert code == 0 and "none" in out


def test_predict_merge_clean_text(d, capsys):
    code, out, _ = run(capsys, "-C", d, "predict", "main", "prod")
    assert code == 0 and "is clean" in out


def test_predict_merge_json(d, capsys):
    code, out, _ = run(capsys, "-C", d, "predict", "prod", "dev", "--json")
    data = json.loads(out)
    hunk = data["files"][0]["hunks"][0]
    assert data["clean"] is False and hunk["preferred"] == "theirs"
    assert hunk["theirs"]["verdict"] == "PROVEN" and hunk["ours"]["verdict"] == "UNVERIFIED"


# -- explain ---------------------------------------------------------------------------

def test_explain_text_and_json(d, capsys):
    picked(d)
    code, out, err = run(capsys, "-C", d, "explain", "--no-ai")
    assert code == 0 and err == ""
    assert "cherry-pick stopped" in out and "Prefer THEIRS" in out
    code, out, _ = run(capsys, "-C", d, "explain", "--no-ai", "--json")
    data = json.loads(out)
    assert data["operation"] == "cherry-pick"
    assert data["files"][0]["hunks"][0]["ai"] is None


def test_explain_after_staging_says_continue(d, capsys):
    picked(d)
    repo = Repo(d)
    (d / "settings.py").write_text(repo.show_file("hotfix-812", "settings.py"))
    repo.run("add", "settings.py")
    code, out, _ = run(capsys, "-C", d, "explain", "--no-ai")
    assert code == 0 and "git cherry-pick --continue" in out


# -- resolve ----------------------------------------------------------------------------

def test_resolve_json(d, capsys):
    picked(d)
    code, out, _ = run(capsys, "-C", d, "resolve", "--no-ai", "--json")
    data = json.loads(out)
    assert code == 0 and data["commit"] and data["unresolved"] == [] and data["empty"] is False


def test_resolve_no_commit(d, capsys):
    picked(d)
    code, out, _ = run(capsys, "-C", d, "resolve", "--no-ai", "--no-commit")
    assert code == 0 and "no commit, as asked" in out
    assert Repo(d).in_progress_operation() is not None


def test_resolve_tie_exits_1(tmp_path, capsys):
    repo = Repo(tmp_path)
    repo.run("init", "-q", "-b", "main")
    (tmp_path / "a").write_text("1\n")
    repo.run("add", "a")
    repo.run("commit", "-qm", "base")
    repo.run("checkout", "-qb", "o")
    (tmp_path / "a").write_text("2\n")
    repo.run("commit", "-qam", "o")
    repo.run("checkout", "-q", "main")
    (tmp_path / "a").write_text("3\n")
    repo.run("commit", "-qam", "m")
    repo.run_raw("merge", "o")
    code, out, _ = run(capsys, "-C", tmp_path, "resolve", "--no-ai")
    assert code == 1 and "Left for you: a" in out
    code, out, _ = run(capsys, "-C", tmp_path, "resolve", "--no-ai", "--json")
    assert code == 1 and json.loads(out)["unresolved"] == ["a"]


def test_resolve_prefer_ai_without_gemma_falls_back(d, capsys):
    picked(d)
    code, out, err = run(capsys, "-C", d, "resolve", "--prefer", "ai")
    assert code == 0 and "falling back" in err and "theirs (proven)" in out


def test_resolve_interactive(d, capsys, monkeypatch):
    picked(d)
    answers = iter(["x", "g", "o"])          # unknown key, Gemma unavailable, then ours
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    code, out, _ = run(capsys, "-C", d, "resolve", "-i", "--no-ai")
    assert code == 0 and "Gemma is not available" in out
    assert "SESSION_TIMEOUT = 7200" in (d / "settings.py").read_text()


@pytest.mark.parametrize("key, expected", [("t", "SESSION_TIMEOUT = 1800"), ("p", "SESSION_TIMEOUT = 1800")])
def test_resolve_interactive_theirs_and_provenance(d, capsys, monkeypatch, key, expected):
    picked(d)
    monkeypatch.setattr(builtins, "input", lambda prompt="": key)
    assert run(capsys, "-C", d, "resolve", "-i", "--no-ai")[0] == 0
    assert expected in (d / "settings.py").read_text()


def test_resolve_interactive_skip(d, capsys, monkeypatch):
    picked(d)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "s")
    code, out, _ = run(capsys, "-C", d, "resolve", "-i", "--no-ai")
    assert code == 1 and "<<<<<<<" in (d / "settings.py").read_text()


def test_resolve_empty_pick_message(tmp_path, capsys):
    repo = Repo(tmp_path)
    repo.run("init", "-q", "-b", "main")
    (tmp_path / "a").write_text("1\n")
    repo.run("add", "a")
    repo.run("commit", "-qm", "base")
    (tmp_path / "a").write_text("2\n")
    repo.run("commit", "-qam", "two")
    two = repo.must_rev("HEAD")
    (tmp_path / "a").write_text("5\n")
    repo.run("commit", "-qam", "five")
    repo.run_raw("revert", "--no-edit", two)
    code, out, _ = run(capsys, "-C", tmp_path, "resolve", "--prefer", "ours", "--no-ai")
    assert code == 0 and "git revert --skip" in out


# -- envs / setup / sync / report ----------------------------------------------------------

def test_envs_text(d, capsys):
    code, out, _ = run(capsys, "-C", d, "envs")
    assert code == 0 and "prod  prod" in out and "dev   dev" in out


def test_envs_bad_set(d, capsys):
    with pytest.raises(SystemExit, match="name=branch"):
        main(["-C", str(d), "envs", "--set", "prod"])


def test_envs_none_found(tmp_path, capsys):
    Repo(tmp_path).run("init", "-q", "-b", "main")
    code, out, _ = run(capsys, "-C", tmp_path, "envs")
    assert code == 0 and "No environment branches found" in out


def test_report_without_envs_is_an_error(tmp_path, capsys):
    repo = Repo(tmp_path)
    repo.run("init", "-q", "-b", "main")
    (tmp_path / "a").write_text("1\n")
    repo.run("add", "a")
    repo.run("commit", "-qm", "base")
    code, _, err = run(capsys, "-C", tmp_path, "report", "-o", tmp_path / "r.html")
    assert code == 2 and "No environment branches" in err


def test_report_writes_html(d, tmp_path, capsys):
    out_file = tmp_path / "ledger.html"
    code, out, _ = run(capsys, "-C", d, "report", "-o", out_file)
    html = out_file.read_text()
    assert code == 0 and "3 changes across prod, uat, dev" in out
    assert html.count("<tr>") == 4 and "missing" in html and "proven" in html


def test_setup_is_idempotent(d, capsys):
    Repo(d).run("remote", "add", "origin", str(d))
    for _ in range(2):
        assert run(capsys, "-C", d, "setup")[0] == 0
    repo = Repo(d)
    assert repo.run("config", "--get-all", "remote.origin.fetch").count(NOTES_REF) == 2  # src and dst
    assert repo.run("config", "--get-all", "notes.rewriteRef").splitlines() == [NOTES_REF]


def test_sync_to_empty_remote(d, tmp_path, capsys):
    bare = tmp_path / "r.git"
    Repo(tmp_path).run("init", "-q", "--bare", str(bare))
    Repo(d).run("remote", "add", "origin", str(bare))
    code, out, _ = run(capsys, "-C", d, "sync")
    assert code == 0 and "No provenance notes on origin yet" in out and "Pushed notes" in out
    assert Repo(bare).rev(NOTES_REF)
    code, out, _ = run(capsys, "-C", d, "sync")
    assert "Fetched notes" in out


# -- general -----------------------------------------------------------------------------

def test_outside_a_repository(tmp_path, capsys):
    code, _, err = run(capsys, "-C", tmp_path, "show")
    assert code == 2 and "error:" in err


def test_demo_into_non_repo_folder_refuses_force(tmp_path, capsys):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "file").write_text("keep me")
    code, _, err = run(capsys, "demo", tmp_path / "x", "--force")
    assert code == 2 and "not one" in err
    assert (tmp_path / "x" / "file").read_text() == "keep me"


def test_module_entry_point():
    out = subprocess.run([sys.executable, "-m", "merge_monkey", "--version"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.startswith("merge-monkey ")


@pytest.mark.parametrize("command", ["mark", "show", "trace", "predict", "explain", "resolve",
                                     "envs", "setup", "sync", "report", "demo"])
def test_every_command_has_help(command, capsys):
    with pytest.raises(SystemExit) as exc:
        main([command, "-h"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("usage: git mm " + command)
