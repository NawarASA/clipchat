import pytest
from conftest import make_project

from clipchat.timeline import (EditError, History, Project, TextOverlay, cut_output_range,
                               remove_source_ranges, subtract_ranges)


def test_subtract_ranges_basic():
    assert subtract_ranges(0, 10, [(2, 3), (5, 6)]) == [(0, 2), (3, 5), (6, 10)]


def test_subtract_ranges_overlapping_and_out_of_bounds():
    assert subtract_ranges(0, 10, [(-5, 1), (4, 7), (6, 8), (9.98, 20)]) == [(1, 4), (8, 9.98)]


def test_subtract_ranges_drops_slivers():
    assert subtract_ranges(0, 10, [(0.01, 10)]) == []


def test_cut_output_range_spans_clips():
    p = make_project(5, 5, 5)  # source 0-5, 5-10, 10-15
    removed = cut_output_range(p, 4, 11)
    assert removed == pytest.approx(7)
    assert [(c.start, c.end) for c in p.clips] == [(0, 4), (11, 15)]


def test_cut_output_range_middle_of_clip_splits_it():
    p = make_project(10)
    cut_output_range(p, 3, 4)
    assert [(c.start, c.end) for c in p.clips] == [(0, 3), (4, 10)]
    assert len({c.id for c in p.clips}) == 2


def test_cut_output_range_validates():
    p = make_project(5)
    with pytest.raises(EditError):
        cut_output_range(p, 3, 2)
    with pytest.raises(EditError):
        cut_output_range(p, 6, 7)


def test_cut_shifts_text_overlays():
    p = make_project(20)
    p.texts = [TextOverlay("before", 0, 2), TextOverlay("after", 10, 12), TextOverlay("inside", 4.5, 5.5)]
    cut_output_range(p, 4, 6)
    by_text = {t.text: (t.start, t.end) for t in p.texts}
    assert by_text["before"] == (0, 2)
    assert by_text["after"] == (8, 10)
    assert "inside" not in by_text  # fully inside the cut


def test_remove_source_ranges_after_reorder():
    p = make_project(5, 5)
    p.clips.reverse()  # source 5-10 plays first
    removed = remove_source_ranges(p, {"m0": [(1, 2), (7, 8)]})
    assert removed == pytest.approx(2)
    assert [(c.start, c.end) for c in p.clips] == [(5, 7), (8, 10), (0, 1), (2, 5)]


def test_offsets_and_summary():
    p = make_project(3, 4)
    assert p.offsets() == [0, 3]
    s = p.summary()
    assert "clip 2: a.mp4" in s and "plays at 0:03.0-0:07.0" in s


def test_roundtrip_dict():
    p = make_project(3, 4)
    p.texts.append(TextOverlay("hi", 0, 1))
    q = Project.from_dict(p.to_dict())
    assert q.to_dict() == p.to_dict()


def test_history_undo_redo():
    h = History()
    p = make_project(5)
    h.push(p)
    p.clips.pop()
    p = h.undo(p)
    assert len(p.clips) == 1
    p = h.redo(p)
    assert len(p.clips) == 0
    with pytest.raises(EditError):
        h.redo(p)
