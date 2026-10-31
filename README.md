# Merge Monkey 🐒

[![CI](https://github.com/Glitch-On/Merge-Monkey/actions/workflows/ci.yml/badge.svg)](https://github.com/Glitch-On/Merge-Monkey/actions/workflows/ci.yml)
[![Gemma](https://github.com/Glitch-On/Merge-Monkey/actions/workflows/gemma.yml/badge.svg)](https://github.com/Glitch-On/Merge-Monkey/actions/workflows/gemma.yml)

Git conflict resolution with testing provenance. Built for Problem 01 at Hacktoberfest 2026
Hack Day Sonipat by team xyz (Vansh Gaur, Simran Sobhani).

**Git shows you two conflicting versions. Merge Monkey also tells you which one is already
proven in production and which one has never been tested.** You still choose, and the result
is an ordinary git commit.

- [The problem](#the-problem)
- [Quick start](#quick-start)
- [Using it on your own repository](#using-it-on-your-own-repository)
- [Commands](#commands)
- [What the verdicts mean](#what-the-verdicts-mean)
- [Common situations](#common-situations)
- [How it works](#how-it-works)
- [Tested on real projects](#tested-on-real-projects)
- [Development](#development)
- [Limits](#limits)

## The problem

Teams keep long-lived branches for each environment and copy fixes between them:

```
prod ──●── hotfix #812 (tested, live) ──────────────►
        \                       backport │
uat  ────●──────────────────────────────┼──────────►
          \                             ▼
dev  ──────●── PR #845 (never tested) ── 💥 conflict
```

When the backport conflicts, plain git gives you this and nothing else:

```
<<<<<<< HEAD
SESSION_TIMEOUT = 7200
SESSION_WARN_BEFORE_EXPIRY = True
=======
SESSION_TIMEOUT = 1800
>>>>>>> 9bb60ff (Fix crash on long sessions (#812))
```

Which side is running in production? Which one passed CI? Was this fix already copied
somewhere else? Git doesn't record any of that. Merge Monkey does:

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

🤖 Gemma (gemma4:e2b, local):
   The conflict involves setting the session timeout. The 'theirs'
   version (1800 seconds) is a proven fix in production, while 'ours'
   proposes a longer timeout (7200 seconds).
   Suggestion: combine — We adopt the proven session timeout from 'theirs' while retaining the warning flag from 'ours'.
   Proposed text:
   │ SESSION_TIMEOUT = 1800
   │ SESSION_WARN_BEFORE_EXPIRY = True
```

The Gemma section is unedited output from a run of the [Gemma workflow](.github/workflows/gemma.yml)
in CI. Gemma's wording and suggestion can differ between runs. The provenance lines above it
do not, because they come from git, not the model.

## Quick start

You need Python 3.10+ and git 2.40+.

```bash
git clone https://github.com/Glitch-On/Merge-Monkey.git
cd Merge-Monkey
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
git mm -h
```

Git runs any program named `git-<name>` on your PATH as `git <name>`, so the tool is used as
`git mm`. Run `source .venv/bin/activate` again in every new terminal, or install it into
an environment that is always active.

Then walk through the built-in demo. It creates a dev/uat/prod repository with a known
backport conflict:

```bash
git mm demo /tmp/mm-demo          # add --force to rebuild it later
cd /tmp/mm-demo
git mm trace hotfix-812           # where is hotfix #812, and where is it missing?
git mm predict --pick prod dev    # which backports will conflict, before running them
git cherry-pick -x hotfix-812     # the conflict happens
git mm explain                    # both sides, with origin and test status
git mm resolve --prefer tested    # keep the proven side and commit
git log -1                        # a normal commit with provenance trailers
git mm show HEAD                  # NEEDS RETEST: the merged code is new
git mm report                     # HTML table of every change across environments
```

The real output of each step is in [`examples/demo-backport/`](examples/demo-backport).

### Optional: Gemma

Everything above works without a model. To get plain-language explanations and a second
opinion on unclear matches, run Gemma 4 locally with Ollama:

```bash
ollama serve                 # in a separate terminal
ollama pull gemma4:e4b       # once, a few GB
git mm explain               # now includes a Gemma section
```

`MM_MODEL` picks another model (for example `gemma4:e2b` on a low-memory laptop) and
`OLLAMA_HOST` points at another Ollama server. Add `--no-ai` to any command to skip Gemma.
Code is never sent to a cloud service.

## Using it on your own repository

**1. Name your environment branches.** Branches called `prod`/`production`,
`uat`/`staging`/`qa` and `dev`/`develop` are found automatically. Otherwise:

```bash
git mm envs --set prod=main --set uat=staging --set dev=develop
```

Remote branches work too, for example `--set prod=upstream/main` when your project is a
fork or was started from someone else's repository.

**2. Share provenance with the team.** Records live in git notes, which a normal clone
does not fetch. Run this once per clone:

```bash
git mm setup      # git fetch now brings the notes; amend and rebase keep them
git mm sync       # fetch the team's records and push yours
```

**3. Record test and deploy results from CI.** Add a step after tests pass or after a
deploy:

```yaml
# after installing merge-monkey in the job
- name: Record provenance
  run: |
    git mm mark "$GITHUB_SHA" --env uat --ci passed --by github-actions
    git mm sync
```

**4. Use it when moving changes.** Run `predict` before a merge or backport, and
`explain` / `resolve` when one stops on a conflict.

With no records at all, Merge Monkey still works from history. A commit reachable from
the prod branch is shown as in prod, marked `inferred` so you can tell it apart from a
recorded fact.

## Commands

| Command | What it does |
|---|---|
| `git mm mark <rev> --env prod --ci passed` | Record a deployment or test result for a commit. Usually run from CI. |
| `git mm show [<rev>]` | The commit's verdict and every piece of evidence behind it. |
| `git mm trace <rev>` | Find the same change on every environment branch, even under a different sha. |
| `git mm predict <from> <into>` | Forecast the conflicts of a merge without touching your files. |
| `git mm predict --pick <from> <into>` | For each commit `<into>` lacks, forecast whether cherry-picking it is clean, conflicts, or is already there. |
| `git mm explain` | During a stopped merge, cherry-pick, revert or rebase: who wrote each side of each hunk and how proven it is. |
| `git mm resolve` | Resolve every hunk and finish with a normal commit. `--prefer tested` (default), `ours`, `theirs` or `ai`; `-i` to choose hunk by hunk. |
| `git mm envs [--set env=branch]` | Show or set which branch is prod, uat and dev. |
| `git mm setup` / `git mm sync` | Make fetch carry provenance notes / fetch and push them. |
| `git mm report [-o file.html]` | One-page table: every change × every environment. |
| `git mm demo <path>` | Build the demo repository. |

Most commands accept `--json`. Use `git mm <command> -h` for help. `git mm --help` does not
work, because git intercepts `--help` and looks for a man page.

## What the verdicts mean

| Verdict | Meaning |
|---|---|
| ✅ `PROVEN` | In production and CI passed on this exact code. |
| ✅ `TESTED` | CI passed on this exact code, but it isn't in production. |
| ⚠️ `IN PROD, NO TEST RECORD` | On the prod branch, with no test record. Common in clones without notes. |
| ⚠️ `IN UAT, NO TEST RECORD` | Same, for uat. |
| 🔁 `NEEDS RETEST` | Only a different version of this change passed, for example a backport edited while resolving a conflict. |
| ❓ `UNVERIFIED` | No deploy or test evidence at all. |
| ❌ `FAILING` | CI failed on this exact code. |

Status only carries from one commit to its copy when the code is identical (same
`git patch-id`). Every piece of evidence is labelled `recorded` (written by `mark`),
`inherited` (from an identical copy or original) or `inferred` (worked out from branches).

## Common situations

| Situation | What to run |
|---|---|
| Hotfix on prod must go back to dev | `git mm trace <fix>`, `git mm predict --pick prod dev`, `git cherry-pick -x <fix>`, then `explain` / `resolve` |
| Release: merge uat into prod | `git mm predict uat prod`, then `git merge` |
| Conflict where one side is tested | `git mm resolve --prefer tested` |
| Conflict where neither or both sides are tested | `git mm resolve -i`, or `--prefer ai` with Gemma running |
| File deleted on one side, edited on the other | `git mm explain`, then `git rm` or `git add` the file and `git <op> --continue` |
| `git pull`, `git rebase` or `git revert` conflicts | Same as a merge: `git mm explain`, `git mm resolve` |
| Pick says "nothing to resolve" | The change is already on the branch: `git cherry-pick --skip` |
| GitHub says the PR has conflicts | `git fetch`, `git merge origin/main` on your branch, `explain`, `resolve`, `git push` |
| A fix was copied without `-x` | `git mm trace <fix>` still finds it by patch-id or similar diff |
| Undo a resolve before pushing | `git reset --hard ORIG_HEAD` after a merge, `git reset --hard HEAD~1` after a pick |

Always cherry-pick with `-x`. It records the original commit in the message, which is the
most reliable link between a fix and its copies.

## How it works

**Recognising the same change on another branch.** A cherry-pick gets a new sha, so Merge
Monkey tries these signals, strongest first:

1. Ancestry: the commit itself is on the branch.
2. Trailers: `(cherry picked from commit X)` written by `git cherry-pick -x` (CPython's
   style), or `Backport of X from main` (Django's style).
3. `git patch-id --stable`: the same diff under a different sha.
4. Fuzzy diff: overlap of the added and removed lines, plus the same subject or issue
   number. This catches backports edited while resolving a conflict. Commits that share
   only removed lines are not matched, because replacing the same old code differently is
   not the same change.
5. Gemma: below 75% confidence, Gemma reads both diffs and confirms or rejects the match.
   Without Gemma, the match is reported as a "possible match" for a person to check.

**Storing provenance.** Records are JSON in git notes under `refs/notes/merge-monkey`.
Notes are ordinary git objects, so they are pushed and fetched like branches, and they
stay out of commit history.

**Explaining a conflict.** For each hunk, Merge Monkey finds each side's text in that
side's version of the file and runs `git blame` on it. It keeps the commits the other side
lacks, since those are the changes that collide, and rates each side by its weakest commit.
If one side is clearly better proven it says so; on a tie it says that and leaves the choice
to you.

**Forecasting.** `predict` uses `git merge-tree --write-tree`, which performs the merge in
memory. Your working tree, index and branches are not touched.

**Finishing with standard git.** `resolve` writes the chosen text, stages it and runs the
operation's own `--continue`, so the result is a normal merge or cherry-pick commit. It adds
trailers that any git can read:

```
(cherry picked from commit 9bb60ffb343d30c9da2beb1d7439d560f90f7909)

Merge-Monkey-Ours: a79204399d0031052a2e1f07ebf16390329e4f15 UNVERIFIED (in dev (inferred from branch), no test record)
Merge-Monkey-Theirs: 9bb60ffb343d30c9da2beb1d7439d560f90f7909 PROVEN (in prod, CI passed)
Merge-Monkey-Resolution: settings.py hunk 1 -> theirs (proven)
```

The new commit gets a note saying it is untested, because the combined code has never run
as a whole. Mark it after CI passes: `git mm mark HEAD --ci passed`.

## Tested on real projects

[`examples/run_real_example.sh`](examples/run_real_example.sh) runs `trace` and `predict` on
local clones of Django (`main`, `stable/6.1.x`) and CPython (`main`, the newest `3.x`), with no
network calls. The saved output is in [`examples/django/`](examples/django) and
[`examples/cpython/`](examples/cpython):

- a Django `[6.1.x] ... Backport of <sha> from main.` commit traced to its original on `main`;
- a CPython `(cherry picked from commit <sha>)` backport traced the same way;
- forecasts of pending backports, each marked clean, conflict or already present. 15 Django
  forecasts take about 8 seconds on a laptop.

## Development

```bash
pip install -e '.[dev]'
ruff check src tests
pytest --cov
```

The 152 tests build real git repositories in temporary folders. The suite is strict:
warnings fail the run, unknown markers or config keys fail, and branch coverage must stay
at or above 95%. [`tests/test_guidelines.py`](tests/test_guidelines.py) maps tests to Problem
01's evaluation scenarios: Django and CPython backport formats, simulated dev/uat/prod repos,
edited backports, provenance moving between clones, clones without notes, and results that
pass `git fsck --strict` and `git interpret-trailers`. Gemma is tested against a fake Ollama
server, so the unit tests need no model.

| Workflow | Runs on | What it does |
|---|---|---|
| [`ci.yml`](.github/workflows/ci.yml) | every PR and push to `main` | secret scan, `ruff`, tests with coverage on Python 3.10, 3.12 and 3.13, the demo end to end, then builds the wheel and runs it in a clean venv |
| [`gemma.yml`](.github/workflows/gemma.yml) | manual run, or a PR that changes the Gemma code | starts Ollama, pulls `gemma4:e2b`, and checks that Gemma explains and resolves the demo conflict |
| [`release.yml`](.github/workflows/release.yml) | pushing a `v*` tag | tests, builds the package and attaches it to a GitHub release |

[`scripts/check_secrets.sh`](scripts/check_secrets.sh) fails the build if anything shaped like
an API key, token or private key is tracked. The only token the workflows use is GitHub's
built-in `GITHUB_TOKEN`.

```
src/merge_monkey/
  cli.py          git mm commands
  identity.py     matching a change across branches
  provenance.py   test/deploy records in git notes, and the verdicts
  conflicts.py    parsing markers and blaming each side
  predict.py      merge-tree forecasts
  resolve.py      applying choices and committing
  ai.py           Gemma through Ollama
  trace.py, report.py, render.py, envs.py, gitutil.py, demo.py
tests/            152 tests
examples/         real output from the demo, Django and CPython
```

## Limits

- `predict --pick` checks each missing commit against the current target on its own.
  Picking several in a row can change later results.
- Cherry-pick one commit at a time. If `git cherry-pick a b c` stops again on a later
  commit, `resolve` reports an error instead of handling the chain.
- Modify/delete, rename and binary conflicts are explained but resolved by hand.
- `git stash pop` conflicts are not supported.
- During a rebase, "ours" is the branch being rebased onto, as in git itself, and rebased
  commits do not get `Merge-Monkey-*` trailers.
- A clean merge can still break the code. Merge Monkey can't see that kind of semantic
  conflict, so it marks every resolution as untested.

## License

[MIT](LICENSE)
