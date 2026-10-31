"""Command line: `git mm <command>` (or `merge-monkey <command>`)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .ai import get_ai
from .conflicts import Analyzer, analyze_worktree
from .envs import configured_envs, set_env
from .gitutil import GitError, Repo
from .predict import predict_merge, predict_picks
from .provenance import NOTES_REF, lookup, mark
from .render import c, file_conflicts, provenance_block, verdict
from .resolve import STRATEGIES, choose, resolve_worktree


def warn(msg: str) -> None:
    print(c(f"warning: {msg}", "yellow"), file=sys.stderr)


def emit_json(data) -> None:
    print(json.dumps(data, indent=2, default=str))


# -- commands ---------------------------------------------------------------

def cmd_mark(repo: Repo, a) -> int:
    rec = mark(repo, a.rev, env=a.env, ci=a.ci, tests=a.tests, by=a.by, note=a.note)
    sha = repo.must_rev(a.rev)
    if a.json:
        emit_json({"sha": sha, "record": rec})
    else:
        what = ", ".join(f"{k}={v}" for k, v in rec.items() if k != "at")
        print(f"✓ Recorded on {sha[:10]}: {what}")
    return 0


def cmd_show(repo: Repo, a) -> int:
    prov = lookup(repo, a.rev)
    if a.json:
        emit_json(prov.to_dict())
        return 0
    print(f"{c(prov.sha[:10], 'yellow')} {prov.subject}")
    print("\n".join(provenance_block(prov)))
    return 0


def cmd_trace(repo: Repo, a) -> int:
    from .trace import trace, trace_to_dict
    ai = get_ai(not a.no_ai, a.model, warn)
    branches = a.branches.split(",") if a.branches else None
    result = trace(repo, a.rev, branches, ai=ai)
    if a.json:
        emit_json(trace_to_dict(result))
        return 0
    print(f"{c(result['sha'][:10], 'yellow')} {result['subject']}  ({result['author']})")
    print(f"  {verdict(result['origin'].verdict)}  {result['origin'].label}\n")
    width = max(len(r["branch"]) for r in result["rows"]) if result["rows"] else 6
    for r in result["rows"]:
        m, prov = r["match"], r["provenance"]
        env = f"[{r['env']}]" if r["env"] else ""
        if m.found:
            status = f"{c(m.sha[:10], 'yellow')}  {m.method} ({m.confidence:.0%})"
            tail = f"   {verdict(prov.verdict)} {prov.label}" if prov else ""
        else:
            status, tail = c("MISSING", "red") + f"  {m.method}", ""
        print(f"  {r['branch']:<{width}} {env:<6} {status}{tail}")
        if m.detail:
            print(f"  {'':<{width}} {'':<6} {c(m.detail, 'dim')}")
    return 0


def cmd_predict(repo: Repo, a) -> int:
    ai = get_ai(a.ai, a.model, warn)
    analyzer = Analyzer(repo, ai=ai)
    if a.pick:
        res = predict_picks(repo, a.source, a.target, analyzer, limit=a.limit, ai=ai)
        if a.json:
            emit_json({**res, "picks": [{**p, "files": [f.to_dict() for f in p["files"]]}
                                         for p in res["picks"]]})
            return 0
        print(f"Changes on {a.source} that {a.target} does not have yet:\n")
        if not res["picks"]:
            print("  none")
        for p in res["picks"]:
            icon = {"clean": c("clean   ", "green"), "conflict": c("CONFLICT", "red"),
                    "already there": c("present ", "dim")}[p["status"]]
            print(f"  {icon} {c(p['sha'][:10], 'yellow')} {p['subject']}")
            if p["status"] == "already there":
                print(f"           {c(p['match']['method'] + ': ' + str(p['match']['sha'])[:10], 'dim')}")
            for f in p["files"]:
                print(f"           {f.path} ({f.kind}, {len(f.hunks)} hunk(s))")
        if res["truncated"]:
            print(c(f"\n  (showing the last {a.limit}; use --limit)", "dim"))
        conflicts = [p for p in res["picks"] if p["status"] == "conflict"]
        if conflicts and a.verbose:
            for p in conflicts:
                print("\n" + file_conflicts(p["files"]))
        return 0

    res = predict_merge(repo, a.source, a.target, analyzer)
    if a.json:
        emit_json({**res, "files": [f.to_dict() for f in res["files"]]})
        return 0
    if res["clean"]:
        print(f"✓ Merging {a.source} into {a.target} is clean.")
        return 0
    n = sum(max(1, len(f.hunks)) for f in res["files"])
    print(f"Merging {a.source} into {a.target} will cause {n} conflict(s):\n")
    print(file_conflicts(res["files"]))
    return 0


def cmd_explain(repo: Repo, a) -> int:
    ai = get_ai(not a.no_ai, a.model, warn)
    analysis = analyze_worktree(repo, ai=ai)
    if a.json:
        emit_json({**analysis, "files": [f.to_dict() for f in analysis["files"]]})
        return 0
    print(f"{analysis['operation']} stopped: ours = {analysis['ours_ref']} "
          f"({analysis['ours'][:10]}), theirs = {analysis['theirs_ref']} ({analysis['theirs'][:10]})\n")
    if not analysis["files"]:
        op = analysis["operation"]
        staged = repo.run_raw("diff", "--cached", "--quiet").code != 0
        if op == "cherry-pick" and not staged:
            from .identity import find_on_branch
            m = find_on_branch(repo, analysis["theirs"], "HEAD")
            where = f" as {m.sha[:10]} ({m.method})" if m.found else ""
            print(f"Nothing to resolve: this change is already on this branch{where}.")
            print("Run `git cherry-pick --skip` to drop the empty pick.")
        else:
            print(f"No conflicted files left. Run `git {op} --continue`.")
        return 0
    print(file_conflicts(analysis["files"]))
    print("\n" + c("Next: git mm resolve --prefer tested   (or --interactive)", "dim"))
    return 0


def _interactive_chooser(hr):
    from .render import hunk_report
    print("\n" + hunk_report(hr, hr.hunk.index + 1, hr.hunk.index + 1))
    options = "[o]urs  [t]heirs  [p]rovenance pick  [g]emma  [s]kip"
    while True:
        ans = input(f"\n{options} > ").strip().lower()[:1]
        if ans == "o":
            return choose(hr, "ours")
        if ans == "t":
            return choose(hr, "theirs")
        if ans == "p":
            lines, why = choose(hr, "tested")
            if lines is None:
                print("  " + why)
                continue
            return lines, why
        if ans == "g":
            if not hr.ai:
                print("  Gemma is not available for this hunk.")
                continue
            lines, why = choose(hr, "ai")
            if lines is None:
                print("  " + why)
                continue
            return lines, why
        if ans == "s":
            return None, "skipped"


def cmd_resolve(repo: Repo, a) -> int:
    use_ai = a.prefer == "ai" or a.interactive
    ai = get_ai(use_ai and not a.no_ai, a.model, warn)
    if a.prefer == "ai" and ai is None:
        warn("falling back to --prefer tested for hunks Gemma cannot decide")
    chooser = _interactive_chooser if a.interactive else None
    res = resolve_worktree(repo, a.prefer, ai=ai, chooser=chooser, commit=not a.no_commit)
    if a.json:
        emit_json({"operation": res.operation, "commit": res.commit, "empty": res.empty,
                   "unresolved": res.unresolved_files,
                   "decisions": [d.__dict__ for d in res.decisions]})
        return 0 if res.complete else 1
    for d in res.decisions:
        mark_ = c("✓", "green") if d.resolved else c("✗", "red")
        print(f"{mark_} {d.path} hunk {d.hunk + 1}: {d.reason}")
    if not res.complete:
        print(c(f"\nLeft for you: {', '.join(res.unresolved_files)} (markers kept where undecided).", "yellow"))
        return 1
    if res.empty:
        print("\nThe resolution keeps none of the incoming change, so there is nothing to commit.")
        print(f"Run `git {res.operation} --skip` to drop it, or edit the files and run "
              f"`git {res.operation} --continue`.")
        return 0
    if res.commit:
        print(f"\n✓ Committed {c(res.commit[:10], 'yellow')} with provenance trailers.")
        print(c("  The resolved code is new: run the tests, then `git mm mark HEAD --ci passed`.", "dim"))
    else:
        print("\n✓ Resolved and staged (no commit, as asked).")
    return 0


def cmd_envs(repo: Repo, a) -> int:
    for item in a.set or []:
        name, _, branch = item.partition("=")
        if not branch:
            raise SystemExit(f"--set expects name=branch, got {item!r}")
        env = set_env(repo, name, branch)
        if not a.json:
            print(f"✓ {env.name} -> {env.branch}")
    envs = configured_envs(repo)
    if a.json:
        emit_json([{"env": e.name, "branch": e.branch} for e in envs])
        return 0
    if not envs:
        print("No environment branches found. Example: git mm envs --set prod=main --set dev=develop")
    for e in envs:
        print(f"  {e.name:<5} {e.branch}")
    return 0


def cmd_setup(repo: Repo, a) -> int:
    remote = a.remote
    refspec = f"+{NOTES_REF}:{NOTES_REF}"
    existing = repo.run_raw("config", "--get-all", f"remote.{remote}.fetch").out.split()
    if refspec not in existing:
        repo.run("config", "--add", f"remote.{remote}.fetch", refspec)
    existing = repo.run_raw("config", "--get-all", "notes.rewriteRef").out.split()
    if NOTES_REF not in existing:
        repo.run("config", "--add", "notes.rewriteRef", NOTES_REF)
    print(f"✓ `git fetch {remote}` now brings provenance notes.")
    print("✓ Notes follow commits through amend and rebase.")
    print(f"  Share yours with: git mm sync --remote {remote}")
    return 0


def cmd_sync(repo: Repo, a) -> int:
    res = repo.run_raw("fetch", a.remote, f"{NOTES_REF}:refs/notes/mm-remote")
    if res.code == 0:
        repo.run_raw("notes", f"--ref={NOTES_REF}", "merge", "-s", "cat_sort_uniq", "refs/notes/mm-remote")
        print(f"✓ Fetched notes from {a.remote}")
    else:
        print(f"  No provenance notes on {a.remote} yet.")
    if repo.rev(NOTES_REF) or repo.run_raw("rev-parse", "-q", "--verify", NOTES_REF).code == 0:
        repo.run("push", a.remote, NOTES_REF)
        print(f"✓ Pushed notes to {a.remote}")
    return 0


def cmd_report(repo: Repo, a) -> int:
    from .report import ledger, render_html
    data = ledger(repo, limit=a.limit)
    out = Path(a.output)
    out.write_text(render_html(data), encoding="utf-8")
    print(f"✓ Wrote {out} ({len(data['rows'])} changes across "
          f"{', '.join(e.name for e in data['envs'])})")
    return 0


def cmd_demo(_repo, a) -> int:
    from .demo import build_demo
    path = Path(a.path)
    if a.force and path.exists():
        if not (path / ".git").exists():
            raise ValueError(f"--force only replaces an existing git repo; {path} is not one")
        import shutil
        shutil.rmtree(path)
    info = build_demo(path)
    print(f"✓ Demo repository created at {info['path']}")
    print("  Branches: dev, uat, prod. Hotfix #812 is tested in prod; dev has untested PR #845.\n")
    print("  Try:")
    print(f"    cd {info['path']}")
    print("    git mm trace hotfix-812")
    print("    git mm predict --pick prod dev")
    print("    git cherry-pick -x hotfix-812      # conflicts")
    print("    git mm explain")
    print("    git mm resolve --prefer tested")
    return 0


# -- parser -------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="git mm",
        description="Merge Monkey: git conflict resolution with testing provenance.")
    p.add_argument("--version", action="version", version=f"merge-monkey {__version__}")
    p.add_argument("-C", dest="repo", default=".", help="run in this repository")
    sub = p.add_subparsers(dest="command", required=True)

    def common(sp, ai=False):
        sp.add_argument("--json", action="store_true", help="machine-readable output")
        if ai:
            sp.add_argument("--no-ai", action="store_true", help="do not call Gemma")
            sp.add_argument("--model", help="Ollama model (default: $MM_MODEL or gemma4:e4b)")

    sp = sub.add_parser("mark", help="record that a commit was tested or deployed")
    sp.add_argument("rev")
    sp.add_argument("--env", help="environment it is deployed to (dev, uat, prod)")
    sp.add_argument("--ci", choices=["passed", "failed"])
    sp.add_argument("--tests", choices=["passed", "failed"])
    sp.add_argument("--by")
    sp.add_argument("--note")
    common(sp)
    sp.set_defaults(func=cmd_mark)

    sp = sub.add_parser("show", help="show the testing provenance of a commit")
    sp.add_argument("rev", nargs="?", default="HEAD")
    common(sp)
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("trace", help="follow a change across branches")
    sp.add_argument("rev")
    sp.add_argument("--branches", help="comma-separated branches (default: environment branches)")
    common(sp, ai=True)
    sp.set_defaults(func=cmd_trace)

    sp = sub.add_parser("predict", help="forecast conflicts before merging or backporting")
    sp.add_argument("source", help="branch with the changes")
    sp.add_argument("target", help="branch that will receive them")
    sp.add_argument("--pick", action="store_true",
                    help="forecast cherry-picking each missing commit instead of a merge")
    sp.add_argument("--limit", type=int, default=30)
    sp.add_argument("-v", "--verbose", action="store_true", help="with --pick, show conflict details")
    sp.add_argument("--ai", action="store_true", help="ask Gemma to explain each predicted hunk")
    sp.add_argument("--model")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_predict)

    sp = sub.add_parser("explain", help="explain the conflicts of a stopped merge/cherry-pick")
    common(sp, ai=True)
    sp.set_defaults(func=cmd_explain)

    sp = sub.add_parser("resolve", help="resolve conflicts and finish with a normal commit")
    sp.add_argument("--prefer", choices=STRATEGIES, default="tested",
                    help="tested: side with stronger provenance (default); ai: Gemma's suggestion")
    sp.add_argument("-i", "--interactive", action="store_true", help="choose hunk by hunk")
    sp.add_argument("--no-commit", action="store_true", help="resolve and stage only")
    common(sp, ai=True)
    sp.set_defaults(func=cmd_resolve)

    sp = sub.add_parser("envs", help="show or set which branch is dev / uat / prod")
    sp.add_argument("--set", action="append", metavar="ENV=BRANCH")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(func=cmd_envs)

    sp = sub.add_parser("setup", help="make fetch/rebase carry provenance notes")
    sp.add_argument("--remote", default="origin")
    sp.set_defaults(func=cmd_setup)

    sp = sub.add_parser("sync", help="fetch and push provenance notes")
    sp.add_argument("--remote", default="origin")
    sp.set_defaults(func=cmd_sync)

    sp = sub.add_parser("report", help="write an HTML ledger of changes across environments")
    sp.add_argument("-o", "--output", default="merge-monkey-report.html")
    sp.add_argument("--limit", type=int, default=60)
    sp.set_defaults(func=cmd_report)

    sp = sub.add_parser("demo", help="create a demo dev/uat/prod repository")
    sp.add_argument("path")
    sp.add_argument("--force", action="store_true", help="replace the folder if it is an old demo repo")
    sp.set_defaults(func=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo = Repo(args.repo)
    try:
        if args.command != "demo":
            repo.git_dir  # fail early outside a repository
        return args.func(repo, args)
    except GitError as exc:
        print(c(f"error: {exc}", "red"), file=sys.stderr)
        return 2
    except (RuntimeError, ValueError, FileExistsError) as exc:
        print(c(f"error: {exc}", "red"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
