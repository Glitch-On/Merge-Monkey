"""Which branch plays which role (dev, uat, prod).

Set explicitly with git config:

    git config mergemonkey.env.prod  release/production
    git config mergemonkey.env.uat   staging
    git config mergemonkey.env.dev   develop

When nothing is configured, common branch names are detected.
"""

from __future__ import annotations

from dataclasses import dataclass

from .gitutil import Repo

# Higher rank = closer to real users = more trusted.
ENV_RANK = {"prod": 3, "uat": 2, "dev": 1}

ALIASES = {
    "prod": "prod", "production": "prod", "live": "prod",
    "uat": "uat", "staging": "uat", "stage": "uat", "qa": "uat", "test": "uat",
    "dev": "dev", "develop": "dev", "development": "dev",
}

DEFAULT_BRANCHES = {
    "prod": ["prod", "production"],
    "uat": ["uat", "staging", "qa"],
    "dev": ["dev", "develop", "development"],
}


@dataclass(frozen=True)
class Env:
    name: str
    branch: str

    @property
    def rank(self) -> int:
        return ENV_RANK.get(self.name, 0)


def normalize_env(name: str) -> str:
    return ALIASES.get(name.lower(), name.lower())


def configured_envs(repo: Repo) -> list[Env]:
    envs: dict[str, str] = {}
    for key, value in repo.config_get_regexp(r"^mergemonkey\.env\."):
        name = normalize_env(key.split(".", 2)[2])
        envs[name] = value.strip()
    if not envs:
        for name, candidates in DEFAULT_BRANCHES.items():
            for branch in candidates:
                if repo.branch_exists(branch):
                    envs[name] = branch
                    break
    result = [Env(n, b) for n, b in envs.items() if repo.branch_exists(b)]
    return sorted(result, key=lambda e: -e.rank)


def set_env(repo: Repo, name: str, branch: str) -> Env:
    env = Env(normalize_env(name), branch)
    repo.run("config", f"mergemonkey.env.{env.name}", branch)
    return env
