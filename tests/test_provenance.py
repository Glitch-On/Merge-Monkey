import pytest

from merge_monkey.provenance import lookup, mark, read_note


def test_demo_hotfix_is_proven(demo):
    prov = lookup(demo["repo"], demo["hotfix"])
    assert prov.verdict == "PROVEN"
    assert prov.deployed["prod"] == "recorded"


def test_untested_feature_is_unverified(demo):
    prov = lookup(demo["repo"], demo["feature"])
    assert prov.verdict == "UNVERIFIED"
    assert prov.deployed == {"dev": "inferred"}


def test_identical_copy_inherits_tests(demo):
    prov = lookup(demo["repo"], demo["hotfix_uat"])
    assert prov.tested
    assert prov.ci_source == "inherited-same"


def test_original_inherits_from_tested_copy(demo):
    # the upload change was tested on its uat copy; the dev original is the same code
    prov = lookup(demo["repo"], demo["upload"])
    assert prov.tested and prov.ci_source == "inherited-same"
    assert prov.deployed.get("uat") == "inherited"


def test_mark_appends_records(demo):
    repo = demo["repo"]
    mark(repo, demo["feature"], ci="failed", by="ci")
    mark(repo, demo["feature"], env="uat", by="ops")
    note = read_note(repo, demo["feature"])
    assert [r.get("ci") for r in note["records"]] == ["failed", None]
    prov = lookup(repo, demo["feature"])
    assert prov.verdict == "FAILING"


def test_mark_validates_input(demo):
    with pytest.raises(ValueError):
        mark(demo["repo"], "HEAD")
    with pytest.raises(ValueError):
        mark(demo["repo"], "HEAD", ci="maybe")


def test_fallback_without_notes(demo):
    repo = demo["repo"]
    repo.run("update-ref", "-d", "refs/notes/merge-monkey")
    prov = lookup(repo, demo["hotfix"])
    assert prov.ci is None
    assert prov.deployed["prod"] == "inferred"
    assert prov.verdict == "IN PROD, NO TEST RECORD"
