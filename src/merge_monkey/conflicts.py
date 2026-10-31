"""Parse conflict markers and work out who wrote each side and whether it was tested."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .envs import Env, configured_envs
from .gitutil import CommitInfo, Repo
from .provenance import Provenance, lookup

MARK_OURS, MARK_BASE, MARK_SEP, MARK_THEIRS = "<<<<<<<", "|||||||", "=======", ">>>>>>>"
BLAME_SHA_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


def _is_marker(line: str, marker: str) -> bool:
    if not line.startswith(marker):
        return False
    rest = line[len(marker):]
    return rest == "" or rest[0] in " \r\n"


@dataclass
class Hunk:
    index: int
    start_line: int                  # 1-based line of the <<<<<<< marker
    ours: list[str]
    theirs: list[str]
    base: list[str] | None = None
    ours_label: str = ""
    theirs_label: str = ""
    base_label: str = ""


@dataclass
class ParsedFile:
    segments: list  # list[list[str] | Hunk]

    @property
    def hunks(self) -> list[Hunk]:
        return [s for s in self.segments if isinstance(s, Hunk)]

    def render(self, choices: dict[int, list[str]]) -> str:
        """Rebuild the file; hunks without a choice keep their markers."""
        out: list[str] = []
        for seg in self.segments:
            if not isinstance(seg, Hunk):
                out.extend(seg)
            elif seg.index in choices:
                out.extend(choices[seg.index])
            else:
                out.append(f"{MARK_OURS} {seg.ours_label}\n".replace(" \n", "\n"))
                out.extend(seg.ours)
                if seg.base is not None:
                    out.append(f"{MARK_BASE} {seg.base_label}\n".replace(" \n", "\n"))
                    out.extend(seg.base)
                out.append(f"{MARK_SEP}\n")
                out.extend(seg.theirs)
                out.append(f"{MARK_THEIRS} {seg.theirs_label}\n".replace(" \n", "\n"))
        return "".join(out)


def parse_conflicts(text: str) -> ParsedFile:
    lines = text.splitlines(keepends=True)
    segments: list = []
    plain: list[str] = []
    i, idx = 0, 0
    while i < len(lines):
        line = lines[i]
        if not _is_marker(line, MARK_OURS):
            plain.append(line)
            i += 1
            continue
        # find the matching separator and end marker
        j = i + 1
        base_at = sep_at = end_at = None
        while j < len(lines):
            if base_at is None and sep_at is None and _is_marker(lines[j], MARK_BASE):
                base_at = j
            elif sep_at is None and _is_marker(lines[j], MARK_SEP):
                sep_at = j
            elif sep_at is not None and _is_marker(lines[j], MARK_THEIRS):
                end_at = j
                break
            j += 1
        if sep_at is None or end_at is None:   # unbalanced: treat as text
            plain.append(line)
            i += 1
            continue
        if plain:
            segments.append(plain)
            plain = []
        ours_end = base_at if base_at is not None else sep_at
        segments.append(Hunk(
            index=idx,
            start_line=i + 1,
            ours=lines[i + 1:ours_end],
            base=lines[base_at + 1:sep_at] if base_at is not None else None,
            theirs=lines[sep_at + 1:end_at],
            ours_label=line[len(MARK_OURS):].strip(),
            theirs_label=lines[end_at][len(MARK_THEIRS):].strip(),
            base_label=lines[base_at][len(MARK_BASE):].strip() if base_at is not None else "",
        ))
        idx += 1
        i = end_at + 1
    if plain:
        segments.append(plain)
    return ParsedFile(segments)


# -- attribution -----------------------------------------------------------

def _find_block(haystack: list[str], needle: list[str]) -> int | None:
    n = len(needle)
    if n == 0:
        return None
    for i in range(len(haystack) - n + 1):
        if haystack[i:i + n] == needle:
            return i
    norm = [" ".join(x.split()) for x in needle]
    for i in range(len(haystack) - n + 1):
        if [" ".join(x.split()) for x in haystack[i:i + n]] == norm:
            return i
    return None


def blame_block(repo: Repo, rev: str, path: str, lines: list[str]) -> list[str]:
    """Commits that last touched `lines` in `path` at `rev`, in file order."""
    content = repo.show_file(rev, path)
    if content is None or not lines:
        return []
    start = _find_block(content.splitlines(), [l.rstrip("\r\n") for l in lines])
    if start is None:
        return []
    out = repo.run_raw("blame", "--porcelain", "-L", f"{start + 1},{start + len(lines)}",
                       rev, "--", path).out
    shas = [m.group(1) for m in map(BLAME_SHA_RE.match, out.splitlines()) if m]
    return list(dict.fromkeys(s for s in shas if not s.startswith("0000000")))


def side_commits(repo: Repo, rev: str, other: str, path: str, lines: list[str]) -> list[str]:
    """Commits on `rev`'s side responsible for this hunk side.

    Keep only commits the other side does not have; those are the changes that
    collide. Fall back to the side's own history of the file.
    """
    blamed = blame_block(repo, rev, path, lines)
    own = [c for c in blamed if not repo.is_ancestor(c, other)]
    if own:
        return own
    log = repo.run_raw("log", "--no-merges", "--format=%H", "--max-count=5",
                       f"{other}..{rev}", "--", path).out.split()
    return log or blamed


@dataclass
class CommitSide:
    info: CommitInfo
    provenance: Provenance
    branches: list[str]

    def to_dict(self) -> dict:
        return {
            "sha": self.info.sha, "subject": self.info.subject, "author": self.info.author,
            "date": self.info.date, "env_branches": self.branches,
            "provenance": self.provenance.to_dict(),
        }


@dataclass
class SideReport:
    name: str          # "ours" / "theirs"
    rev: str
    ref_label: str
    lines: list[str]
    commits: list[CommitSide] = field(default_factory=list)

    @property
    def score(self) -> int:
        # weakest link: a side is only as proven as its least proven commit
        return min((c.provenance.score for c in self.commits), default=0)

    @property
    def verdict(self) -> str:
        if not self.commits:
            return "UNKNOWN"
        weakest = min(self.commits, key=lambda c: c.provenance.score)
        return weakest.provenance.verdict

    def to_dict(self) -> dict:
        return {
            "side": self.name, "rev": self.rev, "ref": self.ref_label,
            "text": "".join(self.lines), "score": self.score, "verdict": self.verdict,
            "commits": [c.to_dict() for c in self.commits],
        }


@dataclass
class HunkReport:
    path: str
    hunk: Hunk
    ours: SideReport
    theirs: SideReport
    recommendation: str
    preferred: str | None          # "ours" | "theirs" | None
    ai: dict | None = None

    def to_dict(self) -> dict:
        return {
            "path": self.path, "hunk": self.hunk.index, "line": self.hunk.start_line,
            "ours": self.ours.to_dict(), "theirs": self.theirs.to_dict(),
            "recommendation": self.recommendation, "preferred": self.preferred,
            "ai": self.ai,
        }


@dataclass
class FileConflict:
    path: str
    kind: str                      # content, modify/delete, add/add, ...
    hunks: list[HunkReport] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "note": self.note,
                "hunks": [h.to_dict() for h in self.hunks]}


def env_branches_containing(repo: Repo, sha: str, envs: list[Env]) -> list[str]:
    return [e.name for e in envs if repo.is_ancestor(sha, e.branch)]


class Analyzer:
    def __init__(self, repo: Repo, envs: list[Env] | None = None, ai=None):
        self.repo = repo
        self.envs = configured_envs(repo) if envs is None else envs
        self.ai = ai
        self._prov: dict[str, CommitSide] = {}

    def commit_side(self, sha: str) -> CommitSide:
        if sha not in self._prov:
            self._prov[sha] = CommitSide(
                info=self.repo.commit_info(sha),
                provenance=lookup(self.repo, sha, self.envs),
                branches=env_branches_containing(self.repo, sha, self.envs),
            )
        return self._prov[sha]

    def side(self, name: str, rev: str, other: str, ref_label: str, path: str,
             lines: list[str]) -> SideReport:
        commits = side_commits(self.repo, rev, other, path, lines)
        return SideReport(name, rev, ref_label, lines, [self.commit_side(c) for c in commits])

    def hunk(self, path: str, hunk: Hunk, ours_rev: str, theirs_rev: str,
             ours_ref: str, theirs_ref: str) -> HunkReport:
        ours = self.side("ours", ours_rev, theirs_rev, ours_ref, path, hunk.ours)
        theirs = self.side("theirs", theirs_rev, ours_rev, theirs_ref, path, hunk.theirs)
        preferred, rec = recommend(ours, theirs)
        report = HunkReport(path, hunk, ours, theirs, rec, preferred)
        if self.ai is not None:
            report.ai = self.ai.explain_conflict(report)
        return report

    def file_text(self, path: str, text: str, ours_rev: str, theirs_rev: str,
                  ours_ref: str, theirs_ref: str, kind: str = "content") -> FileConflict:
        parsed = parse_conflicts(text)
        fc = FileConflict(path, kind)
        for h in parsed.hunks:
            fc.hunks.append(self.hunk(path, h, ours_rev, theirs_rev, ours_ref, theirs_ref))
        return fc


def recommend(ours: SideReport, theirs: SideReport) -> tuple[str | None, str]:
    diff = theirs.score - ours.score
    if abs(diff) < 2:
        return None, "Both sides have similar evidence. Decide from the code itself."
    better, worse = (theirs, ours) if diff > 0 else (ours, theirs)
    return better.name, (
        f"Prefer {better.name.upper()}: {better.verdict.lower()} "
        f"vs {worse.verdict.lower()} on {worse.name}. Keep any genuinely new lines "
        f"from {worse.name} only after testing them."
    )


STATUS_KIND = {
    "UU": "content", "AA": "add/add", "DU": "delete/modify", "UD": "modify/delete",
    "AU": "add/unmerged", "UA": "unmerged/add", "DD": "delete/delete",
}


def worktree_conflicts(repo: Repo) -> list[tuple[str, str]]:
    out = repo.run("status", "--porcelain=v1", "-z", "--untracked-files=no")
    result = []
    for entry in out.split("\0"):
        if len(entry) > 3 and entry[:2] in STATUS_KIND:
            result.append((entry[3:], STATUS_KIND[entry[:2]]))
    return result


def analyze_worktree(repo: Repo, ai=None, envs: list[Env] | None = None) -> dict:
    op = repo.in_progress_operation()
    if op is None:
        raise RuntimeError("No merge, cherry-pick, revert or rebase is stopped on a conflict.")
    operation, ref = op
    ours_rev = repo.must_rev("HEAD")
    theirs_rev = repo.must_rev(ref)
    analyzer = Analyzer(repo, envs, ai)
    head_name = repo.run_raw("symbolic-ref", "--short", "-q", "HEAD").out.strip() or "HEAD"
    files = []
    for path, kind in worktree_conflicts(repo):
        if kind == "content" or kind == "add/add":
            text = (repo.root / path).read_text(encoding="utf-8", errors="replace")
            files.append(analyzer.file_text(path, text, ours_rev, theirs_rev, head_name, ref, kind))
        else:
            fc = FileConflict(path, kind, note=_file_level_note(repo, analyzer, path, kind,
                                                                ours_rev, theirs_rev))
            files.append(fc)
    return {"operation": operation, "ours": ours_rev, "theirs": theirs_rev,
            "ours_ref": head_name, "theirs_ref": ref, "files": files}


def _file_level_note(repo: Repo, analyzer: Analyzer, path: str, kind: str,
                     ours: str, theirs: str) -> str:
    def last(rev: str, other: str) -> str:
        shas = repo.run_raw("log", "--format=%H", "-1", f"{other}..{rev}", "--", path).out.split()
        if not shas:
            return "unchanged"
        cs = analyzer.commit_side(shas[0])
        return f"{cs.info.sha[:10]} '{cs.info.subject}' [{cs.provenance.verdict}: {cs.provenance.label}]"
    return f"ours: {last(ours, theirs)} | theirs: {last(theirs, ours)}"
