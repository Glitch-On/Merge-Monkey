# Merge Monkey

Git conflict resolution with testing provenance. Problem 01 at Hacktoberfest 2026 Hack Day Sonipat.

When a fix is backported between long-lived branches (dev, uat, prod), git shows the two
conflicting blocks and nothing else. Merge Monkey adds what git leaves out: where each side
came from, whether it was tested, and whether it is already running in production. You
still choose, and the result is an ordinary git commit.

```
━━━ settings.py · conflict 1 of 1 · line 5 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OURS  [ours: dev]
  │ SESSION_TIMEOUT = 7200
  │ SESSION_WARN_BEFORE_EXPIRY = True
  a79204399d Longer sessions with expiry warning (#845)
    by Ravi Kumar · on: dev
    ❓ UNVERIFIED  in dev (inferred from branch), no test record

THEIRS  [theirs: CHERRY_PICK_HEAD]
  │ SESSION_TIMEOUT = 1800
  9bb60ffb34 Fix crash on long sessions (#812)
    by Meera Ops · on: prod
    ✅ PROVEN  in prod, CI passed

Provenance says: Prefer THEIRS: proven vs unverified on ours.
```

## Install

Needs Python 3.10+ and git 2.40+.

```bash
pip install -e .
git mm -h              # git finds the git-mm script, so it runs as a git subcommand
```

For explanations and fuzzy matching, run Gemma locally (optional; everything else works without it):

```bash
ollama pull gemma4:e4b
ollama serve
```

`MM_MODEL` changes the model and `OLLAMA_HOST` points at another Ollama server.

## Try it in two minutes

```bash
git mm demo /tmp/mm-demo          # builds a dev/uat/prod repo with a known backport conflict
cd /tmp/mm-demo
git mm trace hotfix-812           # where is hotfix #812, and where is it missing?
git mm predict --pick prod dev    # which backports will conflict, before you run them
git cherry-pick -x hotfix-812     # conflicts
git mm explain                    # both sides, with origin and test status
git mm resolve --prefer tested    # resolves and commits
git log -1                        # plain commit with provenance trailers
git mm report                     # HTML table of every change across environments
```

The real output of each step is in [`examples/demo-backport/`](examples/demo-backport).

## Commands

| Command | What it does |
|---|---|
| `git mm mark <rev> --env prod --ci passed` | Record a deployment or test result for a commit. Run it from CI. |
| `git mm show [<rev>]` | Test and deploy status of a commit, with the evidence behind it. |
| `git mm trace <rev>` | Find the same change on every environment branch. |
| `git mm predict <src> <dst> [--pick]` | Forecast conflicts of a merge, or of cherry-picking each missing commit, without touching the working tree. |
| `git mm explain` | During a stopped merge/cherry-pick/revert/rebase: who wrote each side of each hunk and how proven it is. Gemma adds a summary and a suggestion. |
| `git mm resolve [--prefer tested\|ours\|theirs\|ai] [-i]` | Apply a resolution per hunk and finish the operation with a normal commit. |
| `git mm envs --set prod=<branch>` | Say which branch is prod, uat and dev. Defaults: branches named `prod`, `uat`/`staging`, `dev`/`develop`. |
| `git mm setup` / `git mm sync` | Make `git fetch` bring provenance notes and keep them through rebase; push and pull them. |
| `git mm report` | One-page HTML ledger: each change × each environment. |

Most commands take `--json` for scripting. Use `-h` for help: git catches `git mm --help` and looks for a man page instead.

## How it works

**1. Recognising the same change on another branch.** A cherry-pick gets a new sha, so
Merge Monkey tries these signals in order, strongest first:

1. ancestry: the commit itself is on the branch;
2. trailers: `(cherry picked from commit X)` from `git cherry-pick -x` (CPython's style) or
   `Backport of X from main` (Django's style);
3. `git patch-id --stable`: same diff, different sha;
4. fuzzy diff: overlap of the changed lines, plus same subject or issue number. This catches
   backports whose code was edited while resolving a conflict, where patch-id no longer matches;
5. Gemma: for fuzzy matches below 75% confidence, Gemma reads both diffs and confirms or
   rejects. Without Gemma these are reported as "possible match".

**2. Storing testing provenance.** Records are JSON in git notes under
`refs/notes/merge-monkey`. Notes are plain git objects, so they can be pushed and fetched
(`git mm sync`), and `git mm setup` makes them follow commits through amend and rebase.
Status moves between related commits only when the code is identical (same patch-id). A
backport whose code changed while resolving a conflict reports `NEEDS RETEST` instead of
borrowing the original's green CI.

**3. When a clone has no notes.** Merge Monkey still infers status from history: a commit
reachable from the prod branch is in prod, and trailers link copies to originals. Every line
of evidence is labelled `recorded`, `inherited` or `inferred`, so you can tell a fact from a guess.

**4. Explaining a conflict.** For each hunk, Merge Monkey finds the side's text in that
side's version of the file and runs `git blame` on it. It keeps the commits the other side
does not have, since those are the changes that collide. Each side is rated by its weakest
commit. When one side is clearly better proven, it says so. On a tie it says that too.

**5. Finishing with standard git.** `resolve` writes the chosen text, stages it, and runs the
operation's own `--continue`, so the result is a normal merge or cherry-pick commit. It
adds trailers that any git can read:

```
(cherry picked from commit 9bb60ffb343d30c9da2beb1d7439d560f90f7909)

Merge-Monkey-Ours: a79204399d0031052a2e1f07ebf16390329e4f15 UNVERIFIED (in dev (inferred from branch), no test record)
Merge-Monkey-Theirs: 9bb60ffb343d30c9da2beb1d7439d560f90f7909 PROVEN (in prod, CI passed)
Merge-Monkey-Resolution: settings.py hunk 1 -> theirs (proven)
```

The new commit is marked unverified. The combined code has never run as a whole until CI says so.

## Real repositories

[`examples/run_real_example.sh`](examples/run_real_example.sh) runs `trace` and `predict`
on real backports in local clones of Django (`main`, `stable/6.1.x`) and CPython (`main`,
newest `3.x`). It makes no network calls. Output from 1 November 2026 is in [`examples/django/`](examples/django)
and [`examples/cpython/`](examples/cpython). Both projects' trailer styles are matched,
and forecasting 15 pending Django backports takes about 8 seconds on a laptop.

## Model

`gemma4:e4b` through Ollama, on the developer's machine. Gemma explains conflicts and
judges unclear matches. Matching and provenance are deterministic code and do not depend on
it. Source code is never sent to a cloud service.

## Tests

```bash
pip install -e '.[test]'
pytest
```

The tests build real git repositories in temporary folders. Gemma is tested against a fake
Ollama HTTP server, so CI needs no model.

The suite is strict: warnings fail the run, unknown markers or config keys fail, and
branch coverage must stay at or above 95% (`pytest --cov`). `tests/test_guidelines.py`
maps tests to Problem 01's evaluation scenarios: Django and CPython backport formats,
simulated dev/uat/prod repos, edited backports, moving provenance between clones, clones
without notes, and checking that results are plain git (`git fsck --strict`,
`git interpret-trailers`).

## CI/CD

| Workflow | Runs on | What it does |
|---|---|---|
| `ci.yml` | every pull request and push to `main` | secret scan, `ruff` lint, tests on Python 3.10, 3.12 and 3.13, the demo backport end to end, then builds the wheel and runs it from a clean venv |
| `gemma.yml` | manual run, or a PR that changes the Gemma code | starts Ollama, pulls `gemma4:e2b`, and checks that Gemma explains and resolves the demo conflict |
| `release.yml` | pushing a `v*` tag | runs the tests, builds the package and attaches it to a GitHub release |

`scripts/check_secrets.sh` fails the build if anything shaped like an API key, token or
private key is tracked. The only token the workflows use is GitHub's built-in `GITHUB_TOKEN`.

## Limits

- `predict --pick` checks each missing commit against the current target on its own. Picking several in a row can change later results.
- modify/delete, rename and other file-level conflicts are explained but must be resolved by hand.
- A clean merge can still break the code (a semantic conflict). Merge Monkey cannot see that; it does mark every resolution as needing a fresh test run.
- During a rebase, "ours" is the branch being rebased onto, as in git itself.

## License

MIT
