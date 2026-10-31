from merge_monkey.identity import find_on_branch, parse_message, similarity, subject_key

DJANGO_MSG = """[5.1.x] Fixed #35866 -- Fixed crash when using QuerySet.update().

Thanks Simon for the report.

Backport of 1b2c3d4e5f60718293a4b5c6d7e8f9012a3b4c5d from main."""

CPYTHON_MSG = """[3.13] gh-125118: don't copy arbitrary values to _Bool in the struct module (GH-125169) (#125263)

(cherry picked from commit 87d7315ac57250046372b0d9ae4619ba619c8c87)

Co-authored-by: A Contributor <contributor@example.com>"""


def test_parse_django_backport():
    meta = parse_message(DJANGO_MSG)
    assert meta.origins == ["1b2c3d4e5f60718293a4b5c6d7e8f9012a3b4c5d"]
    assert "#35866" in meta.refs
    assert meta.subject_key == "fixed #35866 -- fixed crash when using queryset.update()."


def test_parse_cpython_backport():
    meta = parse_message(CPYTHON_MSG)
    assert meta.origins == ["87d7315ac57250046372b0d9ae4619ba619c8c87"]
    assert {"#125118", "#125169"} <= meta.refs


def test_subject_key_strips_branch_tags_and_pr_suffix():
    assert subject_key("[3.12] gh-1: Fix it (GH-99)") == subject_key("gh-1: Fix it")


def test_similarity():
    a = frozenset({("+", "x = 1"), ("-", "x = 0")})
    assert similarity(a, a) == 1.0
    assert similarity(a, frozenset()) == 0.0
    assert 0 < similarity(a, frozenset({("+", "x = 1")})) < 1


def _base(s):
    s.write("f.py", "".join(f"line{i} = {i}\n" for i in range(30)))
    s.commit("base")
    s.git("branch", "stable")


def test_match_by_trailer(scratch):
    _base(scratch)
    s = scratch
    s.write("f.py", s.path.joinpath("f.py").read_text().replace("line5 = 5", "line5 = 50"))
    fix = s.commit("Fix five")
    s.git("checkout", "-q", "stable")
    s.git("cherry-pick", "-x", fix)
    m = find_on_branch(s.repo, fix, "stable")
    assert m.found and m.method == "cherry-pick trailer"
    assert m.sha == s.repo.must_rev("stable")


def test_match_by_patch_id_without_trailer(scratch):
    _base(scratch)
    s = scratch
    s.write("f.py", s.path.joinpath("f.py").read_text().replace("line5 = 5", "line5 = 50"))
    fix = s.commit("Fix five")
    s.git("checkout", "-q", "stable")
    s.write("f.py", s.path.joinpath("f.py").read_text().replace("line20 = 20", "line20 = 200"))
    s.commit("unrelated stable change")
    s.git("cherry-pick", fix)          # no -x
    m = find_on_branch(s.repo, fix, "stable")
    assert m.found and m.method == "patch-id"


def test_fuzzy_match_when_backport_was_edited(scratch):
    _base(scratch)
    s = scratch
    text = s.path.joinpath("f.py").read_text()
    s.write("f.py", text.replace("line5 = 5", "line5 = 50").replace("line6 = 6", "line6 = 60")
            .replace("line7 = 7", "line7 = 70").replace("line8 = 8", "line8 = 80"))
    fix = s.commit("Fix lines five to eight (#77)")
    s.git("checkout", "-q", "stable")
    # backport done by hand: one line resolved differently, no trailer
    s.write("f.py", text.replace("line5 = 5", "line5 = 50").replace("line6 = 6", "line6 = 60")
            .replace("line7 = 7", "line7 = 70").replace("line8 = 8", "line8 = 81"))
    s.commit("[stable] Fix lines five to eight (#77)")
    m = find_on_branch(s.repo, fix, "stable")
    assert m.found
    assert m.method in ("similar diff", "possible match")
    assert "same subject" in m.detail


def test_not_found(scratch):
    _base(scratch)
    s = scratch
    s.write("g.py", "new = True\n")
    sha = s.commit("Add g")
    m = find_on_branch(s.repo, sha, "stable")
    assert not m.found


def test_same_commit(scratch):
    _base(scratch)
    m = find_on_branch(scratch.repo, "main", "main")
    assert m.method == "same commit"


def test_shared_removed_line_is_not_a_match(scratch):
    """Both branches replaced 'blue' with different colours: not the same change."""
    s = scratch
    s.write("index.html", "<div>\n<p style=\"color:blue\"></p>\n</div>\n")
    s.commit("blue")
    s.git("branch", "mine")
    s.write("index.html", "<div>\n<p style=\"color:green\"></p>\n</div>\n")
    s.commit("green")
    s.git("checkout", "-q", "mine")
    s.write("index.html", "<div>\n<p style=\"color:red\"></p>\n</div>\n")
    red = s.commit("red")
    m = find_on_branch(s.repo, red, "main")
    assert not m.found
