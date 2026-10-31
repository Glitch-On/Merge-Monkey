"""Forecast conflicts without touching the working tree (git merge-tree)."""

from __future__ import annotations

import re

from .conflicts import Analyzer, FileConflict
from .gitutil import GitError, Repo
from .identity import find_on_branch

CONFLICT_MSG_RE = re.compile(r"^CONFLICT \(([^)]+)\): (.*)$")


def _merge_tree(repo: Repo, ours: str, theirs: str, merge_base: str | None = None):
    args = ["merge-tree", "--write-tree", "--name-only"]
    if merge_base:
        args.append(f"--merge-base={merge_base}")
    res = repo.run_raw(*args, ours, theirs)
    if res.code not in (0, 1):
        raise GitError(args, res.code, res.err)
    lines = res.out.split("\n")
    tree = lines[0].strip()
    paths, messages, i = [], [], 1
    while i < len(lines) and lines[i] != "":
        paths.append(lines[i])
        i += 1
    messages = [l for l in lines[i + 1:] if l]
    return res.code == 0, tree, list(dict.fromkeys(paths)), messages


def _kind_for(path: str, messages: list[str]) -> str:
    for msg in messages:
        m = CONFLICT_MSG_RE.match(msg)
        if m and path in m.group(2):
            return m.group(1)
    return "content"


def _analyze_tree(repo: Repo, analyzer: Analyzer, tree: str, paths: list[str], messages: list[str],
                  ours: str, theirs: str, ours_ref: str, theirs_ref: str) -> list[FileConflict]:
    files = []
    for path in paths:
        kind = _kind_for(path, messages)
        text = repo.run_raw("cat-file", "-p", f"{tree}:{path}")
        if text.code == 0 and "<<<<<<<" in text.out:
            files.append(analyzer.file_text(path, text.out, ours, theirs, ours_ref, theirs_ref, kind))
        else:
            files.append(FileConflict(path, kind, note=next(
                (m for m in messages if path in m and m.startswith("CONFLICT")), "")))
    return files


def predict_merge(repo: Repo, source: str, target: str, analyzer: Analyzer) -> dict:
    """What happens if `source` is merged into `target`."""
    ours, theirs = repo.must_rev(target), repo.must_rev(source)
    clean, tree, paths, messages = _merge_tree(repo, ours, theirs)
    files = [] if clean else _analyze_tree(repo, analyzer, tree, paths, messages,
                                           ours, theirs, target, source)
    return {"mode": "merge", "source": source, "target": target, "clean": clean, "files": files}


def predict_picks(repo: Repo, source: str, target: str, analyzer: Analyzer,
                  limit: int = 30, ai=None) -> dict:
    """For each change on `source` that `target` lacks, forecast its cherry-pick.

    Each pick is checked against the current `target` on its own; picking them in
    sequence can change later results.
    """
    target_sha = repo.must_rev(target)
    out = repo.run("cherry", target, source)
    pending = [line[2:].strip() for line in out.splitlines() if line.startswith("+ ")]
    results = []
    for sha in pending[-limit:]:
        info = repo.commit_info(sha)
        if info.is_merge:
            continue
        match = find_on_branch(repo, sha, target, ai=ai)
        if match.found:
            results.append({"sha": sha, "subject": info.subject, "status": "already there",
                            "match": match.to_dict(), "files": []})
            continue
        clean, tree, paths, messages = _merge_tree(repo, target_sha, sha, merge_base=f"{sha}^")
        files = [] if clean else _analyze_tree(repo, analyzer, tree, paths, messages,
                                               target_sha, sha, target, sha[:10])
        results.append({"sha": sha, "subject": info.subject,
                        "status": "clean" if clean else "conflict", "files": files})
    return {"mode": "pick", "source": source, "target": target, "picks": results,
            "truncated": len(pending) > limit}
