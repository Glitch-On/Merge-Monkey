import os
import subprocess
from pathlib import Path

import pytest

from merge_monkey.demo import build_demo
from merge_monkey.gitutil import Repo


@pytest.fixture(autouse=True)
def isolated_git(tmp_path_factory, monkeypatch):
    """Keep the user's git config and Ollama out of the tests."""
    home = tmp_path_factory.mktemp("home")
    cfg = home / ".gitconfig"
    cfg.write_text("[user]\n\tname = Test User\n\temail = test@example.com\n"
                   "[init]\n\tdefaultBranch = main\n[commit]\n\tgpgsign = false\n")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(cfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")   # nothing listens here
    monkeypatch.delenv("MM_MODEL", raising=False)


@pytest.fixture
def demo(tmp_path):
    info = build_demo(tmp_path / "demo")
    info["repo"] = Repo(info["path"])
    return info


class Scratch:
    """Tiny helper for hand-built repositories."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        self.repo = Repo(path)
        self.repo.run("init", "-q", "-b", "main")

    def write(self, rel, text):
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def commit(self, msg):
        self.repo.run("add", "-A")
        self.repo.run("commit", "-q", "-m", msg)
        return self.repo.must_rev("HEAD")

    def git(self, *args):
        return self.repo.run(*args)


@pytest.fixture
def scratch(tmp_path):
    return Scratch(tmp_path / "scratch")
