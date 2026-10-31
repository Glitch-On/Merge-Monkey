"""Gemma misbehaving: bad JSON, wrong fields, network errors. The tool must stay correct."""

import urllib.error

import pytest

from merge_monkey import render
from merge_monkey.ai import Gemma, _host
from merge_monkey.conflicts import analyze_worktree
from merge_monkey.resolve import choose


@pytest.fixture
def hunk(demo):
    demo["repo"].run_raw("cherry-pick", "-x", demo["hotfix"])
    return analyze_worktree(demo["repo"])["files"][0].hunks[0]


def gemma_returning(monkeypatch, response):
    client = Gemma(model="gemma4:e4b", host="http://127.0.0.1:9")
    monkeypatch.setattr(client, "_post", lambda path, payload: {"response": response})
    return client


def test_non_json_reply_is_ignored(monkeypatch, hunk):
    client = gemma_returning(monkeypatch, "Sure! Keep theirs.")
    assert client.explain_conflict(hunk) is None
    assert "Gemma call failed" in client.last_error


def test_json_list_reply_is_ignored(monkeypatch):
    client = gemma_returning(monkeypatch, "[1, 2]")
    assert client.generate_json("x") is None


def test_unknown_suggestion_becomes_unsure(monkeypatch, hunk):
    client = gemma_returning(monkeypatch, '{"summary": "s", "suggestion": "delete everything"}')
    out = client.explain_conflict(hunk)
    assert out["suggestion"] == "unsure" and out["model"] == "gemma4:e4b"


def test_same_change_without_verdict_is_ignored(monkeypatch):
    client = gemma_returning(monkeypatch, '{"reason": "dunno"}')
    assert client.same_change("a", "d", "b", "d") is None


@pytest.mark.parametrize("raw, expected", [("2.5", 1.0), ("-1", 0.0), ('"high"', 0.5), ("null", 0.5)])
def test_confidence_is_clamped(monkeypatch, raw, expected):
    client = gemma_returning(monkeypatch, f'{{"same": 1, "confidence": {raw}, "reason": "r"}}')
    out = client.same_change("a", "d", "b", "d")
    assert out["same"] is True and out["confidence"] == expected


def test_network_error_is_reported(monkeypatch, hunk):
    client = Gemma(model="gemma4:e4b", host="http://127.0.0.1:9")

    def boom(path, payload):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(client, "_post", boom)
    assert client.explain_conflict(hunk) is None
    assert "connection refused" in client.last_error


def test_prompt_contains_both_sides_and_provenance(monkeypatch, hunk):
    seen = {}
    client = Gemma(model="gemma4:e4b", host="http://127.0.0.1:9")

    def capture(path, payload):
        seen.update(payload, path=path)
        return {"response": '{"summary": "s", "suggestion": "theirs", "reason": "r"}'}

    monkeypatch.setattr(client, "_post", capture)
    client.explain_conflict(hunk)
    prompt = seen["prompt"]
    assert seen["path"] == "/api/generate" and seen["format"] == "json" and seen["stream"] is False
    assert seen["options"]["temperature"] <= 0.2
    assert "SESSION_TIMEOUT = 7200" in prompt and "SESSION_TIMEOUT = 1800" in prompt
    assert "PROVEN" in prompt and "UNVERIFIED" in prompt
    assert "Do not invent facts" in prompt


@pytest.mark.parametrize("value, expected", [
    ("localhost", "http://localhost:11434"),
    ("10.0.0.5:8080", "http://10.0.0.5:8080"),
    ("http://box:11434/", "http://box:11434"),
    ("https://models.example", "https://models.example"),
])
def test_ollama_host_forms(monkeypatch, value, expected):
    monkeypatch.setenv("OLLAMA_HOST", value)
    assert _host() == expected


def test_model_env_override(monkeypatch):
    monkeypatch.setenv("MM_MODEL", "gemma4:e2b")
    assert Gemma().model == "gemma4:e2b"


# -- choose() with every kind of Gemma answer --------------------------------------

def test_choose_combine(hunk):
    hunk.ai = {"suggestion": "combine", "merged": "A = 1\nB = 2", "reason": "both"}
    lines, why = choose(hunk, "ai")
    assert lines == ["A = 1\n", "B = 2\n"] and why.startswith("combined by Gemma")


def test_choose_combine_with_empty_text_falls_back_to_provenance(hunk):
    hunk.ai = {"suggestion": "combine", "merged": "   ", "reason": "?"}
    lines, why = choose(hunk, "ai")
    assert lines == hunk.hunk.theirs and why == "theirs (proven)"


@pytest.mark.parametrize("side", ["ours", "theirs"])
def test_choose_follows_gemma_side(hunk, side):
    hunk.ai = {"suggestion": side, "reason": "because"}
    lines, why = choose(hunk, "ai")
    assert lines == getattr(hunk.hunk, side) and why == f"{side} (Gemma: because)"


def test_choose_unsure_gemma_and_tie_leaves_it_open(hunk):
    hunk.ai = {"suggestion": "unsure"}
    hunk.preferred = None
    assert choose(hunk, "ai") == (None, "Gemma unsure or unavailable and provenance is tied")


def test_choose_rejects_unknown_strategy(hunk):
    with pytest.raises(ValueError, match="unknown strategy"):
        choose(hunk, "random")


# -- rendering -------------------------------------------------------------------------

def test_hunk_report_shows_gemma_block(hunk):
    hunk.ai = {"model": "gemma4:e4b", "summary": "Line one.\nLine two.", "suggestion": "combine",
               "merged": "SESSION_TIMEOUT = 1800\nSESSION_WARN_BEFORE_EXPIRY = True\n", "reason": "keep both"}
    text = render.hunk_report(hunk, 1, 1)
    assert "🤖 Gemma (gemma4:e4b, local):" in text
    assert "Suggestion: combine" in text and "Proposed text:" in text
    assert "│ SESSION_WARN_BEFORE_EXPIRY = True" in text


def test_colour_only_on_a_terminal(monkeypatch):
    monkeypatch.setattr(render, "use_color", lambda: True)
    assert render.c("x", "red") == "\033[31mx\033[0m"
    assert render.rule() .startswith("\033[2m")
    monkeypatch.setattr(render, "use_color", lambda: False)
    assert render.c("x", "red") == "x"
    assert render.verdict("SOMETHING NEW").endswith("SOMETHING NEW")
