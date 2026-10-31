# Hacktoberfest 2026: Hack Day Rishihood

## Team

- Team name: xyz
- Project name: Merge Monkey
- Members:
  - Vansh Gaur
  - Simran Sobhani
## Problem statement

Problem 01: Intelligent Git Conflict Resolution with Testing Provenance
(Developer Productivity, Git Engineering, Version Control).

When a fix is backported or promoted between long-lived branches (dev, UAT,
production, release), merge conflicts show two blocks of code with no context.
The developer can't tell which side came from where, which side was tested, or
which side is already running in production.

Merge Monkey is a git-integrated tool that annotates each side of a conflict
with its origin and testing status, and helps the developer pick a resolution.
The plan:

- Find where each conflicting change came from using `git patch-id`,
  `git cherry`, and the `(cherry picked from commit ...)` trailers written by
  `git cherry-pick -x`.
- Record test and deployment status (tested on UAT, running in production,
  unverified) in `git notes` so it travels with the commits and can be pushed
  and fetched like any other ref. When a clone has no notes, fall back to what
  can be recovered from commit trailers and patch-ids.
- Match changes that were edited during an earlier conflict resolution by
  comparing hunks with a similarity score, since their patch-ids will differ.
- Write the result as an ordinary merge or cherry-pick commit, with provenance
  in commit trailers, so the history works with plain git and needs no special
  tooling to read.

## AI models used

- `gemma4:e4b` running locally through Ollama.

## Why this model

Provenance detection and test-status tracking are deterministic and do not use
a model. Gemma reads the two sides of a conflict hunk together with their
provenance (origin branch, tested or not, in production or not) and writes a
short explanation of what each side does plus a suggested resolution. The
developer still makes the final call.

We picked Gemma 4 because it is open-weight and runs on a laptop, so source
code never leaves the developer's machine. The e4b size fits in the memory of
an ordinary laptop and is quick enough to run once per conflict hunk.
