from merge_monkey.conflicts import parse_conflicts

TEXT = """a
<<<<<<< HEAD
ours 1
ours 2
=======
theirs 1
>>>>>>> feature
b
<<<<<<< HEAD
x
||||||| base
orig
=======
y
>>>>>>> feature
c
"""


def test_parses_two_hunks_with_diff3_base():
    parsed = parse_conflicts(TEXT)
    h1, h2 = parsed.hunks
    assert h1.ours == ["ours 1\n", "ours 2\n"]
    assert h1.theirs == ["theirs 1\n"]
    assert h1.base is None
    assert (h1.ours_label, h1.theirs_label) == ("HEAD", "feature")
    assert h1.start_line == 2
    assert h2.base == ["orig\n"]
    assert h2.ours == ["x\n"] and h2.theirs == ["y\n"]


def test_render_round_trip_and_choices():
    parsed = parse_conflicts(TEXT)
    assert parsed.render({}) == TEXT
    out = parsed.render({0: ["picked\n"], 1: ["y\n"]})
    assert out == "a\npicked\nb\ny\nc\n"


def test_markers_need_exact_prefix():
    text = "<<<<<<<< not a marker\n=======x\n"
    assert parse_conflicts(text).hunks == []


def test_unbalanced_markers_are_left_alone():
    text = "<<<<<<< HEAD\nonly ours\n"
    parsed = parse_conflicts(text)
    assert parsed.hunks == []
    assert parsed.render({}) == text
