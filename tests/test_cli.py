import json

from merge_monkey.cli import main


def run(capsys, *args):
    code = main(list(args))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_demo_and_trace_json(tmp_path, capsys):
    path = tmp_path / "d"
    code, out, _ = run(capsys, "demo", str(path))
    assert code == 0 and "Demo repository created" in out
    code, out, _ = run(capsys, "-C", str(path), "trace", "hotfix-812", "--json", "--no-ai")
    data = json.loads(out)
    branches = {b["branch"]: b for b in data["branches"]}
    assert data["provenance"]["verdict"] == "PROVEN"
    assert branches["dev"]["match"]["sha"] is None


def test_full_cli_flow(tmp_path, capsys):
    path = tmp_path / "d"
    run(capsys, "demo", str(path))
    code, out, _ = run(capsys, "-C", str(path), "predict", "prod", "dev")
    assert "will cause 1 conflict" in out and "PROVEN" in out
    from merge_monkey.gitutil import Repo
    Repo(path).run_raw("cherry-pick", "-x", "hotfix-812")
    code, out, err = run(capsys, "-C", str(path), "explain")
    assert code == 0 and "Gemma disabled" in err and "Prefer THEIRS" in out
    code, out, _ = run(capsys, "-C", str(path), "resolve", "--no-ai")
    assert code == 0 and "Committed" in out
    code, out, _ = run(capsys, "-C", str(path), "show", "HEAD")
    assert "NEEDS RETEST" in out
    code, out, _ = run(capsys, "-C", str(path), "mark", "HEAD", "--ci", "passed", "--env", "dev")
    assert code == 0
    code, out, _ = run(capsys, "-C", str(path), "show", "HEAD", "--json")
    assert json.loads(out)["verdict"] == "TESTED"
    report = tmp_path / "r.html"
    code, out, _ = run(capsys, "-C", str(path), "report", "-o", str(report))
    assert code == 0 and report.read_text().startswith("<!doctype html>")


def test_envs_and_errors(tmp_path, capsys):
    path = tmp_path / "d"
    run(capsys, "demo", str(path))
    code, out, _ = run(capsys, "-C", str(path), "envs", "--set", "production=main", "--json")
    assert {"env": "prod", "branch": "main"} in json.loads(out)
    code, _, err = run(capsys, "-C", str(path), "explain")
    assert code == 2 and "No merge" in err
    code, _, err = run(capsys, "-C", str(path), "show", "nope")
    assert code == 2


def test_setup_configures_notes(tmp_path, capsys):
    path = tmp_path / "d"
    run(capsys, "demo", str(path))
    from merge_monkey.gitutil import Repo
    repo = Repo(path)
    repo.run("remote", "add", "origin", str(tmp_path / "nowhere"))
    code, out, _ = run(capsys, "-C", str(path), "setup")
    assert code == 0
    assert "+refs/notes/merge-monkey:refs/notes/merge-monkey" in repo.run(
        "config", "--get-all", "remote.origin.fetch")


def test_empty_cherry_pick_is_explained(tmp_path, capsys):
    path = tmp_path / "d"
    run(capsys, "demo", str(path))
    from merge_monkey.gitutil import Repo
    repo = Repo(path)
    repo.run_raw("cherry-pick", "-x", "hotfix-812")
    run(capsys, "-C", str(path), "resolve", "--no-ai")
    assert repo.run_raw("cherry-pick", "-x", "hotfix-812").code != 0   # empty pick stops
    code, out, _ = run(capsys, "-C", str(path), "explain", "--no-ai")
    assert code == 0 and "already on this branch" in out and "--skip" in out


def test_demo_force_rebuilds(tmp_path, capsys):
    path = tmp_path / "d"
    run(capsys, "demo", str(path))
    code, _, err = run(capsys, "demo", str(path))
    assert code == 2 and "--force" in err
    code, out, _ = run(capsys, "demo", str(path), "--force")
    assert code == 0 and "created" in out
