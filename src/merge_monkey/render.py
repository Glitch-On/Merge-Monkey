"""Terminal output."""

from __future__ import annotations

import os
import sys
import textwrap

VERDICT_STYLE = {
    "PROVEN": ("green", "✅"),
    "TESTED": ("green", "✅"),
    "IN PROD, NO TEST RECORD": ("yellow", "⚠️ "),
    "IN UAT, NO TEST RECORD": ("yellow", "⚠️ "),
    "NEEDS RETEST": ("yellow", "🔁"),
    "UNVERIFIED": ("red", "❓"),
    "FAILING": ("red", "❌"),
    "UNKNOWN": ("dim", "· "),
}
CODES = {"red": "31", "green": "32", "yellow": "33", "blue": "34", "bold": "1", "dim": "2", "cyan": "36"}


def use_color() -> bool:
    return sys.stdout.isatty() and "NO_COLOR" not in os.environ


def c(text: str, color: str) -> str:
    if not use_color():
        return text
    return f"\033[{CODES[color]}m{text}\033[0m"


def verdict(v: str) -> str:
    color, icon = VERDICT_STYLE.get(v, ("dim", "· "))
    return f"{icon} {c(v, color)}"


def rule(title: str = "", width: int = 72) -> str:
    if not title:
        return c("━" * width, "dim")
    head = f"━━━ {title} "
    return c(head + "━" * max(3, width - len(head)), "bold")


def provenance_block(prov, indent: str = "  ") -> list[str]:
    lines = [f"{indent}{verdict(prov.verdict)}  {prov.label}"]
    for ev in prov.evidence:
        lines.append(f"{indent}  {c(ev.kind + ':', 'dim')} {ev.text}")
    return lines


def side_block(side, title: str) -> list[str]:
    out = [c(f"{title}  [{side.name}: {side.ref_label}]", "bold")]
    text = "".join(side.lines).rstrip("\n") or "(lines deleted on this side)"
    for line in text.splitlines():
        out.append("  │ " + line)
    if not side.commits:
        out.append("  " + c("no commits found for these lines", "dim"))
    for cs in side.commits:
        envs = ", ".join(cs.branches) or "no env branch"
        out.append(f"  {c(cs.info.sha[:10], 'yellow')} {cs.info.subject}")
        out.append(f"    by {cs.info.author} · on: {envs}")
        out.extend(provenance_block(cs.provenance, "    "))
    return out


def hunk_report(hr, number: int, total: int) -> str:
    out = [rule(f"{hr.path} · conflict {number} of {total} · line {hr.hunk.start_line}")]
    out += side_block(hr.ours, "OURS")
    out.append("")
    out += side_block(hr.theirs, "THEIRS")
    out.append("")
    out.append(c("Provenance says: ", "bold") + hr.recommendation)
    if hr.ai:
        out.append("")
        out.append(c(f"🤖 Gemma ({hr.ai.get('model', 'gemma')}, local):", "cyan"))
        for para in (hr.ai.get("summary") or "").split("\n"):
            out += ["   " + l for l in textwrap.wrap(para, 68)]
        out.append(f"   Suggestion: {hr.ai.get('suggestion')} — {hr.ai.get('reason', '')}")
        merged = (hr.ai.get("merged") or "").rstrip("\n")
        if hr.ai.get("suggestion") == "combine" and merged:
            out.append("   Proposed text:")
            out += ["   │ " + l for l in merged.splitlines()]
    return "\n".join(out)


def file_conflicts(files) -> str:
    blocks = []
    total = sum(len(f.hunks) for f in files)
    n = 0
    for fc in files:
        if not fc.hunks:
            blocks.append(rule(f"{fc.path} · {fc.kind} conflict"))
            if fc.note:
                blocks.append("  " + fc.note)
            continue
        for hr in fc.hunks:
            n += 1
            blocks.append(hunk_report(hr, n, total))
    return "\n\n".join(blocks)
