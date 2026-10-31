"""One-page HTML ledger: every change across dev / uat / prod with its status."""

from __future__ import annotations

import html

from .envs import Env, configured_envs
from .gitutil import Repo
from .identity import load_commits, patch_ids
from .provenance import lookup


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def ledger(repo: Repo, envs: list[Env] | None = None, limit: int = 60) -> dict:
    envs = configured_envs(repo) if envs is None else envs
    if not envs:
        raise RuntimeError("No environment branches found. Set them with: git mm envs --set prod=<branch>")
    branches = [e.branch for e in envs]
    base = repo.run_raw("merge-base", "--octopus", *branches).out.strip()
    ranges = {e.name: ([f"{base}..{e.branch}"] if base else [e.branch]) for e in envs}

    commits, by_env = {}, {}
    pid_owner: dict[str, str] = {}
    uf = _UnionFind()
    for env in envs:
        recs = load_commits(repo, *ranges[env.name], limit=limit)
        pids = patch_ids(repo, *ranges[env.name], limit=limit)
        by_env[env.name] = {r.sha for r in recs}
        for r in recs:
            commits[r.sha] = r
            uf.find(r.sha)
            pid = pids.get(r.sha)
            if pid:
                if pid in pid_owner:
                    uf.union(r.sha, pid_owner[pid])
                else:
                    pid_owner[pid] = r.sha
    for r in commits.values():
        for origin in r.meta.origins:
            full = repo.rev(origin)
            if full:
                uf.union(r.sha, full)

    groups: dict[str, list[str]] = {}
    for sha in commits:
        groups.setdefault(uf.find(sha), []).append(sha)

    rows = []
    for members in groups.values():
        # the original is the member that names no origin
        original = next((m for m in members if not commits[m].meta.origins), members[0])
        cells = {}
        for env in envs:
            present = [m for m in members if m in by_env[env.name]]
            if present:
                prov = lookup(repo, present[0], envs)
                cells[env.name] = {"sha": present[0], "verdict": prov.verdict, "label": prov.label}
            else:
                cells[env.name] = None
        rows.append({"subject": commits[original].subject, "sha": original, "cells": cells})
    return {"envs": envs, "rows": rows}


ICON = {"PROVEN": "✅", "TESTED": "✅", "FAILING": "❌", "UNVERIFIED": "❓",
        "IN PROD, NO TEST RECORD": "⚠️", "IN UAT, NO TEST RECORD": "⚠️", "NEEDS RETEST": "🔁"}
CLASS = {"PROVEN": "ok", "TESTED": "ok", "FAILING": "bad", "UNVERIFIED": "unk"}


def render_html(data: dict, title: str = "Merge Monkey ledger") -> str:
    envs = data["envs"]
    head = "".join(f"<th>{html.escape(e.name)}<br><small>{html.escape(e.branch)}</small></th>" for e in envs)
    body = []
    for row in data["rows"]:
        cells = []
        for env in envs:
            cell = row["cells"][env.name]
            if cell is None:
                cells.append('<td class="miss" title="change not on this branch">⚠️ missing</td>')
            else:
                cls = CLASS.get(cell["verdict"], "warn")
                tip = html.escape(f"{cell['sha'][:10]}: {cell['label']}")
                cells.append(f'<td class="{cls}" title="{tip}">{ICON.get(cell["verdict"], "·")} '
                             f'{html.escape(cell["verdict"].lower())}<br><code>{cell["sha"][:8]}</code></td>')
        body.append(f"<tr><td class='subj'>{html.escape(row['subject'])}"
                    f"<br><code>{row['sha'][:8]}</code></td>{''.join(cells)}</tr>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
 body {{ font: 14px/1.4 system-ui, sans-serif; margin: 2rem; color: #1d1d1f; }}
 table {{ border-collapse: collapse; }}
 th, td {{ border: 1px solid #ddd; padding: .5rem .8rem; text-align: center; }}
 td.subj {{ text-align: left; max-width: 28rem; }}
 .ok {{ background: #e7f6ec; }} .unk {{ background: #fdeaea; }}
 .bad {{ background: #f8d0d0; }} .warn {{ background: #fff6dc; }} .miss {{ background: #f2f2f2; color: #888; }}
 code {{ color: #666; font-size: 12px; }}
</style></head><body>
<h1>{html.escape(title)}</h1>
<p>Each row is one change, matched across branches by cherry-pick trailers and patch-id.
Hover a cell for details.</p>
<table><tr><th>Change</th>{head}</tr>
{''.join(body)}
</table></body></html>
"""
