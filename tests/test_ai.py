"""Gemma integration, against a fake Ollama server (CI has no model)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from merge_monkey.ai import Gemma, get_ai
from merge_monkey.conflicts import analyze_worktree
from merge_monkey.identity import find_on_branch
from merge_monkey.resolve import resolve_worktree

EXPLAIN = {
    "summary": "Prod lowers the timeout to fix #812. Dev raises it and adds a warning flag.",
    "suggestion": "combine",
    "merged": "SESSION_TIMEOUT = 1800\nSESSION_WARN_BEFORE_EXPIRY = True\n",
    "reason": "keep the tested timeout and the new flag",
}


class FakeOllama:
    def __init__(self, models=("gemma4:e4b",), same=True):
        self.models = list(models)
        self.same = same
        self.prompts = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send({"models": [{"name": m} for m in outer.models]})

            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.prompts.append(req)
                if "compare two git commits" in req["prompt"]:
                    answer = {"same": outer.same, "confidence": 0.8, "reason": "same fix"}
                else:
                    answer = EXPLAIN
                self._send({"model": req["model"], "response": json.dumps(answer)})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def ollama(monkeypatch):
    fake = FakeOllama()
    monkeypatch.setenv("OLLAMA_HOST", fake.url)
    yield fake
    fake.close()


def test_unavailable_when_server_down():
    warnings = []
    assert get_ai(True, warn=warnings.append) is None
    assert "not reachable" in warnings[0]


def test_unavailable_when_model_not_pulled(monkeypatch):
    fake = FakeOllama(models=("llama3:8b",))
    monkeypatch.setenv("OLLAMA_HOST", fake.url)
    try:
        client = Gemma()
        assert not client.available()
        assert "ollama pull gemma4:e4b" in client.last_error
    finally:
        fake.close()


def test_host_without_scheme(monkeypatch):
    monkeypatch.setenv("OLLAMA_HOST", "0.0.0.0")
    assert Gemma().host == "http://0.0.0.0:11434"


def test_explain_uses_gemma_with_provenance(demo, ollama):
    demo["repo"].run_raw("cherry-pick", "-x", demo["hotfix"])
    ai = get_ai(True)
    assert ai is not None and ai.model == "gemma4:e4b"
    analysis = analyze_worktree(demo["repo"], ai=ai)
    hr = analysis["files"][0].hunks[0]
    assert hr.ai["suggestion"] == "combine"
    prompt = ollama.prompts[-1]["prompt"]
    assert "PROVEN" in prompt and "UNVERIFIED" in prompt
    assert ollama.prompts[-1]["model"] == "gemma4:e4b"


def test_resolve_with_gemma_combination(demo, ollama):
    repo = demo["repo"]
    repo.run_raw("cherry-pick", "-x", demo["hotfix"])
    res = resolve_worktree(repo, "ai", ai=get_ai(True))
    assert res.complete
    text = (repo.root / "settings.py").read_text()
    assert "SESSION_TIMEOUT = 1800\nSESSION_WARN_BEFORE_EXPIRY = True\n" in text
    assert "combined by Gemma" in repo.commit_info("HEAD").message


def _edited_backport(scratch):
    s = scratch
    s.write("f.py", "".join(f"v{i} = {i}\n" for i in range(40)))
    s.commit("base")
    s.git("branch", "stable")
    text = (s.path / "f.py").read_text()
    s.write("f.py", text.replace("v3 = 3", "v3 = 30").replace("v20 = 20", "v20 = 200"))
    fix = s.commit("Fix values (#9)")
    s.git("checkout", "-q", "stable")
    s.write("f.py", text.replace("v3 = 3", "v3 = 31").replace("v20 = 20", "v20 = 200"))
    s.commit("Patch values for stable")
    return fix


def test_gemma_confirms_unclear_match(scratch, ollama):
    fix = _edited_backport(scratch)
    without = find_on_branch(scratch.repo, fix, "stable")
    assert without.method == "possible match"
    with_ai = find_on_branch(scratch.repo, fix, "stable", ai=get_ai(True))
    assert with_ai.found and "Gemma confirmed" in with_ai.method


def test_gemma_can_reject_unclear_match(scratch, ollama):
    ollama.same = False
    fix = _edited_backport(scratch)
    m = find_on_branch(scratch.repo, fix, "stable", ai=get_ai(True))
    assert not m.found and "Gemma rejected" in m.method
