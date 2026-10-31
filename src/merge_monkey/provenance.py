"""Testing provenance: was this change tested, and where is it running?

Records live in git notes under refs/notes/merge-monkey, one JSON document per
commit. Notes are ordinary git objects, so they can be pushed and fetched like
branches (`git mm sync`).

When a clone has no notes, Merge Monkey falls back to evidence it can still
read from history: which environment branches contain the commit, and
cherry-pick / backport trailers that link copies to their originals. Every
piece of evidence says whether it was recorded or inferred.
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field

from .envs import ENV_RANK, Env, configured_envs, normalize_env
from .gitutil import Repo
from .identity import parse_message, patch_id

NOTES_REF = "refs/notes/merge-monkey"
RESULTS = ("passed", "failed")


def read_note(repo: Repo, sha: str) -> dict | None:
    res = repo.run_raw("notes", f"--ref={NOTES_REF}", "show", sha)
    if res.code != 0:
        return None
    try:
        data = json.loads(res.out)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def write_note(repo: Repo, sha: str, data: dict) -> None:
    repo.run("notes", f"--ref={NOTES_REF}", "add", "-f", "-m",
             json.dumps(data, indent=2, sort_keys=True), sha)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def mark(repo: Repo, rev: str, env: str | None = None, ci: str | None = None,
         tests: str | None = None, by: str | None = None, note: str | None = None,
         at: str | None = None) -> dict:
    """Append a test/deploy record to a commit's note."""
    if ci and ci not in RESULTS:
        raise ValueError(f"--ci must be one of {RESULTS}")
    if tests and tests not in RESULTS:
        raise ValueError(f"--tests must be one of {RESULTS}")
    if not (env or ci or tests or note):
        raise ValueError("nothing to record: give --env, --ci, --tests or --note")
    sha = repo.must_rev(rev)
    data = read_note(repo, sha) or {"version": 1, "records": []}
    if by is None:
        by = repo.run_raw("config", "user.name").out.strip() or None
    record = {k: v for k, v in {
        "env": normalize_env(env) if env else None, "ci": ci, "tests": tests,
        "by": by, "note": note, "at": at or _now(),
    }.items() if v}
    data.setdefault("records", []).append(record)
    write_note(repo, sha, data)
    return record


@dataclass
class Evidence:
    kind: str        # "recorded" | "inherited" | "inferred"
    text: str
    via: str | None = None   # sha the evidence came from, when not this commit


@dataclass
class Provenance:
    sha: str
    subject: str
    deployed: dict[str, str] = field(default_factory=dict)   # env -> recorded|inherited|inferred
    ci: str | None = None          # passed | failed | None
    ci_source: str | None = None   # recorded | inherited-same | inherited-changed
    evidence: list[Evidence] = field(default_factory=list)
    resolution_of: list[dict] = field(default_factory=list)

    @property
    def top_env(self) -> str | None:
        if not self.deployed:
            return None
        return max(self.deployed, key=lambda e: ENV_RANK.get(e, 0))

    @property
    def tested(self) -> bool:
        return self.ci == "passed" and self.ci_source in ("recorded", "inherited-same")

    @property
    def score(self) -> int:
        s = 0
        for env, how in self.deployed.items():
            weight = {"recorded": 3, "inherited": 3, "inferred": 2}[how]
            s = max(s, ENV_RANK.get(env, 0) * weight)
        if self.ci == "passed":
            s += {"recorded": 4, "inherited-same": 3, "inherited-changed": 1}.get(self.ci_source or "", 0)
        elif self.ci == "failed":
            s -= 6
        return s

    @property
    def verdict(self) -> str:
        if self.ci == "failed":
            return "FAILING"
        if self.top_env == "prod" and self.tested:
            return "PROVEN"
        if self.tested:
            return "TESTED"
        if self.ci == "passed":          # only a different version of the code passed
            return "NEEDS RETEST"
        if self.top_env == "prod":
            return "IN PROD, NO TEST RECORD"
        if self.top_env == "uat":
            return "IN UAT, NO TEST RECORD"
        return "UNVERIFIED"

    @property
    def label(self) -> str:
        parts = []
        env = self.top_env
        if env:
            how = self.deployed[env]
            suffix = {"recorded": "", "inferred": " (inferred from branch)",
                      "inherited": " (via linked commit)"}[how]
            parts.append(f"in {env}{suffix}")
        else:
            parts.append("not in any tracked environment")
        if self.ci == "passed":
            suffix = {"recorded": "", "inherited-same": " (on identical original)",
                      "inherited-changed": " (on original; code changed when copied)"}
            parts.append("CI passed" + suffix.get(self.ci_source or "", ""))
        elif self.ci == "failed":
            parts.append("CI FAILED")
        else:
            parts.append("no test record")
        return ", ".join(parts)

    def to_dict(self) -> dict:
        return {
            "sha": self.sha, "subject": self.subject, "verdict": self.verdict,
            "label": self.label, "score": self.score, "deployed": self.deployed,
            "ci": self.ci, "ci_source": self.ci_source,
            "evidence": [e.__dict__ for e in self.evidence],
        }


def _apply_records(prov: Provenance, records: list[dict], kind: str, via: str | None,
                   same_code: bool = True) -> None:
    for rec in records:
        who = f" by {rec['by']}" if rec.get("by") else ""
        when = f" at {rec['at']}" if rec.get("at") else ""
        prefix = "" if via is None else f"[{via[:10]}] "
        caveat = "" if same_code else " (different code: not counted as deployed)"
        if rec.get("env"):
            env = rec["env"]
            # A deployment only carries over when the code is identical.
            if (kind == "recorded" or same_code) and prov.deployed.get(env) != "recorded":
                prov.deployed[env] = "recorded" if kind == "recorded" else "inherited"
            prov.evidence.append(Evidence(kind, f"{prefix}deployed to {env}{who}{when}{caveat}", via))
        for key in ("ci", "tests"):
            result = rec.get(key)
            if not result:
                continue
            source = "recorded" if kind == "recorded" else (
                "inherited-same" if same_code else "inherited-changed")
            rank = {"recorded": 3, "inherited-same": 2, "inherited-changed": 1}
            prov.evidence.append(Evidence(kind, f"{prefix}{key} {result}{who}{when}", via))
            if result == "failed" and source == "inherited-changed":
                continue    # a failure of different code says little about this one
            # A failure of this code always wins; otherwise the strongest source wins.
            if result == "failed" or prov.ci != "failed" and (
                    prov.ci is None or rank[source] >= rank.get(prov.ci_source or "", 0)):
                prov.ci, prov.ci_source = result, source
        if rec.get("note"):
            prov.evidence.append(Evidence(kind, f"{prefix}note: {rec['note']}", via))


def _copies_of(repo: Repo, sha: str, envs: list[Env]) -> list[str]:
    """Commits on environment branches whose trailers say they copy `sha`."""
    if not envs:
        return []
    out = repo.run_raw("log", "--no-merges", "--format=%H%x00%B%x00%x00",
                       f"--grep={sha[:7]}", *[e.branch for e in envs]).out
    copies = []
    for chunk in out.split("\x00\x00"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        h, _, body = chunk.partition("\x00")
        if any(repo.rev(o) == sha for o in parse_message(body).origins):
            copies.append(h)
    return list(dict.fromkeys(copies))


def lookup(repo: Repo, rev: str, envs: list[Env] | None = None) -> Provenance:
    sha = repo.must_rev(rev)
    envs = configured_envs(repo) if envs is None else envs
    info = repo.commit_info(sha)
    prov = Provenance(sha=sha, subject=info.subject)

    note = read_note(repo, sha)
    if note:
        _apply_records(prov, note.get("records", []), "recorded", None)
        prov.resolution_of = note.get("derived_from", [])

    for env in envs:
        if repo.is_ancestor(sha, env.branch):
            if env.name not in prov.deployed:
                prov.deployed[env.name] = "inferred"
            prov.evidence.append(Evidence("inferred", f"reachable from {env.name} branch '{env.branch}'"))

    # Related commits: originals named in our trailers, and copies that name us.
    related: list[tuple[str, str]] = []
    seen = {sha}
    frontier = [sha]
    for _ in range(3):
        nxt = []
        for s in frontier:
            msg = repo.commit_info(s).message
            for origin in parse_message(msg).origins:
                full = repo.rev(origin)
                if full and full not in seen:
                    seen.add(full)
                    related.append((full, "original"))
                    nxt.append(full)
            for copy in _copies_of(repo, s, envs):
                if copy not in seen:
                    seen.add(copy)
                    related.append((copy, "copy"))
                    nxt.append(copy)
        frontier = nxt
        if not frontier:
            break

    my_pid = patch_id(repo, sha) if related else None
    for other, relation in related:
        same = my_pid is not None and patch_id(repo, other) == my_pid
        code = "same code" if same else "code differs"
        other_note = read_note(repo, other)
        if other_note:
            _apply_records(prov, other_note.get("records", []), "inherited", other, same_code=same)
        for env in envs:
            if repo.is_ancestor(other, env.branch):
                if same and env.name not in prov.deployed:
                    prov.deployed[env.name] = "inferred"
                prov.evidence.append(Evidence(
                    "inferred", f"[{other[:10]}] its {relation} ({code}) is on {env.name} branch '{env.branch}'",
                    other))
    return prov
