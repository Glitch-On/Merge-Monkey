"""Follow one change across environment branches."""

from __future__ import annotations

from .envs import Env, configured_envs
from .gitutil import Repo
from .identity import Match, find_on_branch
from .provenance import Provenance, lookup


def trace(repo: Repo, rev: str, branches: list[str] | None = None, ai=None,
          envs: list[Env] | None = None) -> dict:
    envs = configured_envs(repo) if envs is None else envs
    sha = repo.must_rev(rev)
    info = repo.commit_info(sha)
    if branches:
        targets = [(b, next((e.name for e in envs if e.branch == b), "")) for b in branches]
    else:
        targets = [(e.branch, e.name) for e in envs]
        if not targets:
            targets = [(b, "") for b in repo.local_branches()]
    rows = []
    for branch, env_name in targets:
        match: Match = find_on_branch(repo, sha, branch, ai=ai)
        prov: Provenance | None = lookup(repo, match.sha, envs) if match.found else None
        rows.append({"branch": branch, "env": env_name, "match": match, "provenance": prov})
    return {"sha": sha, "subject": info.subject, "author": info.author, "rows": rows,
            "origin": lookup(repo, sha, envs)}


def trace_to_dict(result: dict) -> dict:
    return {
        "sha": result["sha"], "subject": result["subject"], "author": result["author"],
        "provenance": result["origin"].to_dict(),
        "branches": [{
            "branch": r["branch"], "env": r["env"], "match": r["match"].to_dict(),
            "provenance": r["provenance"].to_dict() if r["provenance"] else None,
        } for r in result["rows"]],
    }
