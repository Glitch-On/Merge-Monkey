"""Apply a resolution and finish with an ordinary git commit.

The commit message gets plain-text trailers describing where each side came
from and how each hunk was resolved, so the provenance survives in any clone,
even one without Merge Monkey's notes. The new commit also gets a note saying
it has not been tested yet: the combination is new code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .conflicts import HunkReport, analyze_worktree, parse_conflicts
from .gitutil import Repo
from .provenance import read_note, write_note

STRATEGIES = ("tested", "ours", "theirs", "ai")


def _as_lines(text: str) -> list[str]:
    if not text:
        return []
    if not text.endswith("\n"):
        text += "\n"
    return text.splitlines(keepends=True)


def choose(report: HunkReport, strategy: str) -> tuple[list[str] | None, str]:
    """Return (replacement lines, reason) or (None, reason) when undecided."""
    if strategy == "ours":
        return report.hunk.ours, "ours (requested)"
    if strategy == "theirs":
        return report.hunk.theirs, "theirs (requested)"
    if strategy == "ai":
        ai = report.ai or {}
        sugg = ai.get("suggestion")
        if sugg == "combine" and ai.get("merged", "").strip():
            return _as_lines(ai["merged"]), f"combined by Gemma: {ai.get('reason', '')}".strip()
        if sugg in ("ours", "theirs"):
            lines = report.hunk.ours if sugg == "ours" else report.hunk.theirs
            return lines, f"{sugg} (Gemma: {ai.get('reason', '')})"
        if report.preferred is None:
            return None, "Gemma unsure or unavailable and provenance is tied"
        strategy = "tested"
    if strategy == "tested":
        if report.preferred is None:
            return None, "tie: both sides have similar evidence"
        lines = report.hunk.ours if report.preferred == "ours" else report.hunk.theirs
        side = report.ours if report.preferred == "ours" else report.theirs
        return lines, f"{report.preferred} ({side.verdict.lower()})"
    raise ValueError(f"unknown strategy {strategy!r}; use one of {STRATEGIES}")


@dataclass
class Decision:
    path: str
    hunk: int
    reason: str
    resolved: bool


@dataclass
class ResolveResult:
    decisions: list[Decision] = field(default_factory=list)
    unresolved_files: list[str] = field(default_factory=list)
    commit: str | None = None
    operation: str = ""
    empty: bool = False     # resolution leaves nothing to commit (pick/revert only)

    @property
    def complete(self) -> bool:
        return not self.unresolved_files


def _trailers(analysis: dict, decisions: list[Decision]) -> list[str]:
    lines, seen = [], set()
    for fc in analysis["files"]:
        for hr in fc.hunks:
            for side, key in ((hr.ours, "Ours"), (hr.theirs, "Theirs")):
                for c in side.commits:
                    tag = (key, c.info.sha)
                    if tag in seen:
                        continue
                    seen.add(tag)
                    lines.append(f"Merge-Monkey-{key}: {c.info.sha} "
                                 f"{c.provenance.verdict} ({c.provenance.label})")
    for d in decisions:
        if d.resolved:
            lines.append(f"Merge-Monkey-Resolution: {d.path} hunk {d.hunk + 1} -> {d.reason}")
    return lines


def resolve_worktree(repo: Repo, strategy: str = "tested", ai=None,
                     chooser: Callable[[HunkReport], tuple[list[str] | None, str]] | None = None,
                     commit: bool = True) -> ResolveResult:
    analysis = analyze_worktree(repo, ai=ai)
    result = ResolveResult(operation=analysis["operation"])

    for fc in analysis["files"]:
        if fc.kind not in ("content", "add/add"):
            result.unresolved_files.append(fc.path)
            result.decisions.append(Decision(fc.path, 0, f"{fc.kind} conflict: resolve by hand", False))
            continue
        path = repo.root / fc.path
        parsed = parse_conflicts(path.read_text(encoding="utf-8", errors="replace"))
        choices = {}
        for hr in fc.hunks:
            lines, reason = chooser(hr) if chooser else choose(hr, strategy)
            if lines is not None:
                choices[hr.hunk.index] = list(lines)
            result.decisions.append(Decision(fc.path, hr.hunk.index, reason, lines is not None))
        path.write_text(parsed.render(choices), encoding="utf-8")
        if len(choices) == len(fc.hunks):
            repo.run("add", "--", fc.path)
        else:
            result.unresolved_files.append(fc.path)

    if not result.complete or not commit:
        return result

    if analysis["operation"] in ("cherry-pick", "revert") and repo.ok("diff", "--cached", "--quiet", "HEAD"):
        # Keeping only our side undid the whole pick/revert. git refuses an
        # empty commit here, so leave the choice of --skip to the user.
        result.empty = True
        return result

    gd = repo.git_dir
    msg_file = gd / "MERGE_MSG"
    base_msg = msg_file.read_text(encoding="utf-8") if msg_file.exists() else ""
    kept = [l for l in base_msg.splitlines() if not l.startswith("#")]
    while kept and not kept[-1].strip():
        kept.pop()
    trailers = _trailers(analysis, result.decisions)
    message = "\n".join(kept) + ("\n\n" + "\n".join(trailers) if trailers else "") + "\n"

    op = analysis["operation"]
    env = {"GIT_EDITOR": "true"}
    if op in ("merge", "cherry-pick", "revert"):
        msg_file.write_text(message, encoding="utf-8")
        repo.run(op, "--continue", env=env)
    else:  # rebase keeps its own message handling
        repo.run("rebase", "--continue", env=env)

    new_sha = repo.must_rev("HEAD")
    result.commit = new_sha
    note = read_note(repo, new_sha) or {"version": 1, "records": []}
    note["derived_from"] = [
        {"side": side.name, "sha": c.info.sha, "verdict": c.provenance.verdict}
        for fc in analysis["files"] for hr in fc.hunks for side in (hr.ours, hr.theirs)
        for c in side.commits
    ]
    note["resolution"] = [d.__dict__ for d in result.decisions]
    note["status"] = "unverified: resolved code has not been tested as a whole"
    write_note(repo, new_sha, note)
    return result
