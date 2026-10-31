"""Gemma 4, running locally through Ollama.

Gemma does the two jobs that need judgment:

* decide whether two commits whose diffs only partly match are the same change
  (a backport that was edited while resolving a conflict), and
* explain a conflict hunk using both sides' provenance and suggest a resolution.

It never makes the final call. Provenance and matching work without it, so
`--no-ai` or a stopped Ollama only removes the explanations.

Settings: MM_MODEL (default gemma4:e4b) and OLLAMA_HOST (default
http://localhost:11434), so a team can point at the event's model server.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_MODEL = "gemma4:e4b"
DEFAULT_HOST = "http://localhost:11434"
MAX_SIDE_CHARS = 3000

SAME_CHANGE_PROMPT = """You compare two git commits from different branches of one project.
Decide if commit B is a copy (backport or cherry-pick) of commit A, possibly edited
while resolving a merge conflict. Unrelated changes that touch the same file are NOT the same.

Commit A message:
{msg_a}

Commit A diff:
{diff_a}

Commit B message:
{msg_b}

Commit B diff:
{diff_b}

Reply with JSON only: {{"same": true|false, "confidence": 0.0-1.0, "reason": "<one sentence>"}}"""

EXPLAIN_PROMPT = """You help a developer resolve a git conflict during a {operation}.
File: {path}

OURS ({ours_ref}) text:
{ours_text}
OURS provenance: {ours_prov}

THEIRS ({theirs_ref}) text:
{theirs_text}
THEIRS provenance: {theirs_prov}

Provenance tells you which side is proven in production and which is untested.
Do not invent facts that are not shown above. Explain what each side changes,
then suggest a resolution. Prefer keeping tested behaviour; keep genuinely new
untested lines only if they do not undo the tested fix.

Reply with JSON only:
{{"summary": "<2-3 plain sentences>",
  "suggestion": "ours" | "theirs" | "combine" | "unsure",
  "merged": "<full replacement text for this hunk when suggestion is combine, else empty>",
  "reason": "<one sentence>"}}"""


def _host() -> str:
    host = os.environ.get("OLLAMA_HOST", DEFAULT_HOST).strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        # Ollama's own convention: "0.0.0.0" or "host:port" without a scheme
        if ":" not in host:
            host += ":11434"
        host = "http://" + host
    return host


class Gemma:
    def __init__(self, model: str | None = None, host: str | None = None, timeout: float = 180):
        self.model = model or os.environ.get("MM_MODEL", DEFAULT_MODEL)
        self.host = (host or _host()).rstrip("/")
        self.timeout = timeout
        self.last_error: str | None = None

    # -- transport --------------------------------------------------------

    def _post(self, path: str, payload: dict) -> dict:
        req = urllib.request.Request(
            self.host + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode())

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(self.host + "/api/tags", timeout=3) as resp:
                tags = json.loads(resp.read().decode())
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.last_error = f"Ollama not reachable at {self.host}: {exc}"
            return False
        names = {m.get("name", "") for m in tags.get("models", [])}
        if self.model not in names and f"{self.model}:latest" not in names:
            self.last_error = f"model {self.model} not pulled; run: ollama pull {self.model}"
            return False
        return True

    def generate_json(self, prompt: str) -> dict | None:
        try:
            data = self._post("/api/generate", {
                "model": self.model, "prompt": prompt, "stream": False,
                "format": "json", "options": {"temperature": 0.1},
            })
            text = data.get("response", "")
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            self.last_error = f"Gemma call failed: {exc}"
            return None

    # -- tasks ------------------------------------------------------------

    def same_change(self, msg_a: str, diff_a: str, msg_b: str, diff_b: str) -> dict | None:
        out = self.generate_json(SAME_CHANGE_PROMPT.format(
            msg_a=msg_a, diff_a=diff_a, msg_b=msg_b, diff_b=diff_b))
        if out is None or "same" not in out:
            return None
        out["same"] = bool(out["same"])
        try:
            out["confidence"] = max(0.0, min(1.0, float(out.get("confidence", 0.5))))
        except (TypeError, ValueError):
            out["confidence"] = 0.5
        out["model"] = self.model
        return out

    def explain_conflict(self, report) -> dict | None:
        def prov(side) -> str:
            if not side.commits:
                return "unknown (no commits found for these lines)"
            return "; ".join(
                f"{c.info.sha[:10]} '{c.info.subject}' -> {c.provenance.verdict}: {c.provenance.label}"
                for c in side.commits)

        prompt = EXPLAIN_PROMPT.format(
            operation="merge or cherry-pick", path=report.path,
            ours_ref=report.ours.ref_label, theirs_ref=report.theirs.ref_label,
            ours_text="".join(report.hunk.ours)[:MAX_SIDE_CHARS] or "(empty: lines deleted)",
            theirs_text="".join(report.hunk.theirs)[:MAX_SIDE_CHARS] or "(empty: lines deleted)",
            ours_prov=prov(report.ours), theirs_prov=prov(report.theirs),
        )
        out = self.generate_json(prompt)
        if out is None:
            return None
        if out.get("suggestion") not in ("ours", "theirs", "combine", "unsure"):
            out["suggestion"] = "unsure"
        out["model"] = self.model
        return out


def get_ai(enabled: bool, model: str | None = None, warn=None):
    """Return a ready Gemma client, or None (with a warning) when unavailable."""
    if not enabled:
        return None
    client = Gemma(model=model)
    if client.available():
        return client
    if warn:
        warn(f"Gemma disabled: {client.last_error}")
    return None
