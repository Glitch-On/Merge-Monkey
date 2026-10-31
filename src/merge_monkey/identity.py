"""Recognise the same change on different branches.

A cherry-picked commit gets a new sha, so Merge Monkey matches changes with a
ladder of signals, strongest first:

1. ancestry      - the commit itself is on the branch
2. trailers      - "(cherry picked from commit X)" written by `git cherry-pick -x`,
                   or Django's "Backport of X from main"
3. patch-id      - `git patch-id --stable`: identical diff, different sha
4. fuzzy diff    - most changed lines agree, which happens when a conflict was
                   resolved by hand during the backport
5. Gemma         - asked only for the unclear fuzzy cases
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .gitutil import Repo

CHERRY_RE = re.compile(r"\(cherry picked from commit ([0-9a-f]{7,40})\)")
BACKPORT_RE = re.compile(r"Backport of ([0-9a-f]{7,40}) from (\S+?)\.?$", re.MULTILINE)
MM_TRAILER_RE = re.compile(r"^Merge-Monkey-(?:Ours|Theirs): ([0-9a-f]{7,40})\b", re.MULTILINE)
REF_RE = re.compile(r"(?:(?<![\w/])#|\bGH-|\bgh-|\bbpo-)(\d{2,7})\b")
TAG_PREFIX_RE = re.compile(r"^\s*\[[^\]]{1,40}\]\s*")
TRAILING_REF_RE = re.compile(r"\s*\((?:GH-|gh-|#)\d+\)\s*$")

DIFF_TEXT_LIMIT = 6000
_START = "%x00%x00mm-commit%x00"
_END = "%x00mm-diff%x00%x00"


@dataclass
class MessageRefs:
    origins: list[str] = field(default_factory=list)   # shas this commit was copied from
    refs: set[str] = field(default_factory=set)        # issue / PR numbers
    subject_key: str = ""


def subject_key(subject: str) -> str:
    s = subject
    while TAG_PREFIX_RE.match(s):
        s = TAG_PREFIX_RE.sub("", s, count=1)
    s = TRAILING_REF_RE.sub("", s)
    return " ".join(s.lower().split())


def parse_message(message: str) -> MessageRefs:
    origins = CHERRY_RE.findall(message) + [m[0] for m in BACKPORT_RE.findall(message)]
    subject = message.splitlines()[0] if message else ""
    return MessageRefs(
        origins=list(dict.fromkeys(origins)),
        refs={f"#{n}" for n in REF_RE.findall(message)},
        subject_key=subject_key(subject),
    )


@dataclass
class CommitRecord:
    sha: str
    parents: list[str]
    message: str
    diff: str
    lines: frozenset  # {(sign, normalized line)}

    @property
    def meta(self) -> MessageRefs:
        return parse_message(self.message)

    @property
    def subject(self) -> str:
        return self.message.splitlines()[0] if self.message else ""


def _diff_lines(diff: str) -> frozenset:
    out = set()
    for line in diff.splitlines():
        if line.startswith(("+++", "---")):
            continue
        if line[:1] in "+-":
            norm = " ".join(line[1:].split())
            if norm:
                out.add((line[0], norm))
    return frozenset(out)


# Results keyed by immutable sha ranges, so caching is always safe.
_CACHE: dict[tuple, object] = {}


def _cached(key: tuple, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def _immutable(repo: Repo, rev_args: tuple[str, ...]) -> tuple[str, ...] | None:
    """Resolve refs in a rev range to shas; None if any part is not a plain rev."""
    out = []
    for arg in rev_args:
        if arg.startswith("-"):
            out.append(arg)
            continue
        parts = arg.split("..")
        shas = [repo.rev(p) if p else "" for p in parts]
        if any(s is None for s in shas):
            return None
        out.append("..".join(shas))
    return tuple(out)


def load_commits(repo: Repo, *rev_args: str, limit: int = 5000) -> list[CommitRecord]:
    """Load non-merge commits with their -U0 diffs in one git call."""
    key = _immutable(repo, rev_args)
    if key is None:
        return _load_commits(repo, rev_args, limit)
    return _cached(("commits", str(repo.path), key, limit), lambda: _load_commits(repo, key, limit))


def _load_commits(repo: Repo, rev_args, limit: int) -> list[CommitRecord]:
    out = repo.run(
        "log", "--no-merges", "-p", "-U0", "--no-color", "--no-ext-diff",
        f"--max-count={limit}", f"--format={_START}%H%x00%P%x00%B{_END}", *rev_args,
    )
    records = []
    # NUL never appears in git's text diff output, so NUL-framed markers are safe.
    for chunk in out.split(_START.replace("%x00", "\x00"))[1:]:
        head, _, diff = chunk.partition(_END.replace("%x00", "\x00"))
        sha, parents, message = head.split("\x00", 2)
        records.append(CommitRecord(
            sha=sha,
            parents=parents.split(),
            message=message.strip("\n"),
            diff=diff.strip("\n"),
            lines=_diff_lines(diff),
        ))
    return records


def patch_ids(repo: Repo, *rev_args: str, limit: int = 5000) -> dict[str, str]:
    """Map sha -> stable patch-id for every non-merge commit in the range."""
    key = _immutable(repo, rev_args)
    if key is None:
        return _patch_ids(repo, rev_args, limit)
    return _cached(("pids", str(repo.path), key, limit), lambda: _patch_ids(repo, key, limit))


def _patch_ids(repo: Repo, rev_args, limit: int) -> dict[str, str]:
    log = repo.run("log", "--no-merges", "-p", "--no-color", "--no-ext-diff",
                   f"--max-count={limit}", "--format=commit %H", *rev_args)
    if not log.strip():
        return {}
    out = repo.run("patch-id", "--stable", input=log)
    result = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2:
            pid, sha = parts
            result[sha] = pid
    return result


def patch_id(repo: Repo, sha: str) -> str | None:
    return patch_ids(repo, "-1", sha).get(repo.must_rev(sha))


def similarity(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class Match:
    branch: str
    sha: str | None
    method: str
    confidence: float
    detail: str = ""

    @property
    def found(self) -> bool:
        return self.sha is not None

    def to_dict(self) -> dict:
        return {
            "branch": self.branch, "sha": self.sha, "method": self.method,
            "confidence": round(self.confidence, 2), "detail": self.detail,
        }


def _commit_record(repo: Repo, sha: str) -> CommitRecord | None:
    recs = load_commits(repo, "-1", sha)
    return recs[0] if recs else None


def find_on_branch(repo: Repo, sha: str, branch: str, ai=None) -> Match:
    """Find the commit on `branch` that carries the same change as `sha`."""
    sha = repo.must_rev(sha)
    if repo.is_ancestor(sha, branch):
        return Match(branch, sha, "same commit", 1.0, "the commit itself is on this branch")

    source = _commit_record(repo, sha)
    if source is None:
        return Match(branch, None, "merge commit", 0.0, "merge commits are traced through their parents")

    meta = source.meta
    # The commit may itself be a copy; its original could be on the branch.
    for origin in meta.origins:
        full = repo.rev(origin)
        if full and repo.is_ancestor(full, branch):
            return Match(branch, full, "original of this copy", 1.0,
                         f"{sha[:10]} says it was copied from {full[:10]}")

    identity = {sha} | {repo.rev(o) or o for o in meta.origins}
    branch_sha = repo.must_rev(branch)
    base = repo.merge_base(sha, branch_sha)
    rng = [f"{base}..{branch_sha}"] if base else [branch_sha]
    candidates = load_commits(repo, *rng)

    for cand in candidates:
        cand_origins = {repo.rev(o) or o for o in cand.meta.origins}
        if cand_origins & identity:
            how = "backport note" if "Backport of" in cand.message else "cherry-pick trailer"
            return Match(branch, cand.sha, how, 1.0,
                         f"{cand.sha[:10]} names {sorted(cand_origins & identity)[0][:10]} as its source")

    pids = patch_ids(repo, *rng)
    src_pid = patch_id(repo, sha)
    if src_pid:
        for cand in candidates:
            if pids.get(cand.sha) == src_pid:
                return Match(branch, cand.sha, "patch-id", 0.95, "identical diff, different commit")

    best, best_score = None, 0.0
    for cand in candidates:
        score = similarity(source.lines, cand.lines)
        same_subject = bool(meta.subject_key) and cand.meta.subject_key == meta.subject_key
        shared_refs = bool(meta.refs & cand.meta.refs)
        bonus = 0.25 if same_subject else 0.0
        bonus += 0.15 if shared_refs else 0.0
        total = min(score + bonus, 0.99)
        if total > best_score:
            best, best_score = (cand, score, same_subject, shared_refs), total

    if best is None or best_score < 0.3:
        return Match(branch, None, "not found", 0.0, "no commit on this branch carries this change")

    cand, score, same_subject, shared_refs = best
    reasons = [f"{score:.0%} of changed lines match"]
    if same_subject:
        reasons.append("same subject")
    if shared_refs:
        reasons.append("same issue reference")

    if best_score >= 0.75:
        return Match(branch, cand.sha, "similar diff", best_score, ", ".join(reasons))

    if ai is not None:
        verdict = ai.same_change(source.message, source.diff[:DIFF_TEXT_LIMIT],
                                 cand.message, cand.diff[:DIFF_TEXT_LIMIT])
        if verdict is not None:
            reasons.append(f"Gemma: {verdict.get('reason', '').strip()}")
            if verdict.get("same"):
                conf = max(best_score, float(verdict.get("confidence", 0.7)))
                return Match(branch, cand.sha, "similar diff (Gemma confirmed)", conf, "; ".join(reasons))
            return Match(branch, None, "not found (Gemma rejected closest match)", 0.0,
                         f"closest was {cand.sha[:10]}: " + "; ".join(reasons))

    return Match(branch, cand.sha, "possible match", best_score,
                 ", ".join(reasons) + " (unconfirmed, review by hand)")
