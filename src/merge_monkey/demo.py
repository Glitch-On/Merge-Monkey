"""Build a small dev / uat / prod repository with a known backport conflict.

Used by the tests, the examples and the live demo:

    git mm demo /tmp/mm-demo
    cd /tmp/mm-demo
    git cherry-pick -x hotfix-812     # conflicts
    git mm explain

Story:
* prod gets hotfix #812 (timeout 3600 -> 1800). It is deployed and CI passed.
* uat receives the hotfix by cherry-pick -x, and a tested upload-limit change.
* dev has PR #845 (timeout 3600 -> 7200 plus a new flag). Never tested.
* Backporting #812 into dev conflicts on the timeout line.
"""

from __future__ import annotations

from pathlib import Path

from .gitutil import Repo
from .provenance import mark

SETTINGS_V0 = """\
# Application settings
DEBUG = False

# Sessions
SESSION_TIMEOUT = 3600

# Database
DB_POOL_SIZE = 5
DB_TIMEOUT = 30

# Uploads
MAX_UPLOAD_MB = 10
"""

APP_V0 = """\
from settings import SESSION_TIMEOUT


def session_expired(age_seconds):
    return age_seconds > SESSION_TIMEOUT
"""

_clock = {"t": 1_793_400_000}   # fixed epoch so demo shas are reproducible


def _env(name="Asha Dev", email="asha@example.com") -> dict:
    _clock["t"] += 60
    stamp = f"{_clock['t']} +0530"
    return {
        "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_DATE": stamp,
        "GIT_COMMITTER_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_DATE": stamp,
    }


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _replace(root: Path, rel: str, old: str, new: str) -> None:
    p = root / rel
    text = p.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {rel}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def _commit(repo: Repo, message: str, who=("Asha Dev", "asha@example.com")) -> str:
    repo.run("add", "-A")
    repo.run("commit", "-q", "-m", message, env=_env(*who))
    return repo.must_rev("HEAD")


def build_demo(path: str | Path) -> dict:
    root = Path(path)
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"{root} is not empty (use --force to rebuild it)")
    root.mkdir(parents=True, exist_ok=True)
    _clock["t"] = 1_793_400_000
    repo = Repo(root)
    repo.run("init", "-q", "-b", "main")
    repo.run("config", "user.name", "Release Bot")
    repo.run("config", "user.email", "release-bot@example.com")
    repo.run("config", "commit.gpgsign", "false")

    _write(root, "settings.py", SETTINGS_V0)
    _write(root, "app.py", APP_V0)
    base = _commit(repo, "Initial project skeleton")
    for b in ("dev", "uat", "prod"):
        repo.run("branch", b, base)
    for name in ("prod", "uat", "dev"):
        repo.run("config", f"mergemonkey.env.{name}", name)

    # dev: upload limit change (#850), later promoted to uat and tested there
    repo.run("checkout", "-q", "dev")
    _replace(root, "settings.py", "MAX_UPLOAD_MB = 10", "MAX_UPLOAD_MB = 25")
    upload = _commit(repo, "Raise upload limit to 25 MB (#850)", ("Ravi Kumar", "ravi@example.com"))

    # dev: PR #845 touches the timeout line, never tested
    _replace(root, "settings.py", "SESSION_TIMEOUT = 3600",
             "SESSION_TIMEOUT = 7200\nSESSION_WARN_BEFORE_EXPIRY = True")
    feature = _commit(repo, "Longer sessions with expiry warning (#845)",
                      ("Ravi Kumar", "ravi@example.com"))

    # prod: hotfix #812
    repo.run("checkout", "-q", "prod")
    _replace(root, "settings.py", "SESSION_TIMEOUT = 3600", "SESSION_TIMEOUT = 1800")
    _replace(root, "app.py", "return age_seconds > SESSION_TIMEOUT",
             "return age_seconds >= SESSION_TIMEOUT")
    hotfix = _commit(repo, "Fix crash on long sessions (#812)\n\n"
                           "Sessions older than an hour hit a token refresh bug.",
                     ("Meera Ops", "meera@example.com"))
    repo.run("tag", "hotfix-812", hotfix)

    # uat: gets the upload change and the hotfix, both by cherry-pick -x
    repo.run("checkout", "-q", "uat")
    repo.run("cherry-pick", "-x", upload, env=_env())
    upload_uat = repo.must_rev("HEAD")
    repo.run("cherry-pick", "-x", hotfix, env=_env())
    hotfix_uat = repo.must_rev("HEAD")

    # test and deploy records
    mark(repo, hotfix, env="prod", ci="passed", by="ci-bot", at="2026-10-30T09:00:00+00:00")
    mark(repo, upload_uat, env="uat", ci="passed", by="ci-bot", at="2026-10-30T10:00:00+00:00")

    repo.run("checkout", "-q", "dev")
    return {
        "path": str(root), "base": base, "hotfix": hotfix, "hotfix_uat": hotfix_uat,
        "upload": upload, "upload_uat": upload_uat, "feature": feature,
    }
