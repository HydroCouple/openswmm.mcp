"""Unit tests for ``openswmm_mcp._util.formatting`` helpers.

Specifically the Phase 4d ``paginate_list`` primitive that every O(N)
list-returning tool delegates to.  The helper has no engine dependency
so the tests are pure-Python (no ``openswmm.engine`` import needed).
"""

from __future__ import annotations

from openswmm_mcp._util.formatting import paginate_list, truncate_list

# ---------------------------------------------------------------------------
# truncate_list (unchanged primitive — sanity-only)
# ---------------------------------------------------------------------------


class TestTruncateList:
    def test_under_limit_returns_unchanged(self):
        items, was_trunc = truncate_list([1, 2, 3], max_items=5)
        assert items == [1, 2, 3]
        assert was_trunc is False

    def test_over_limit_truncates_with_flag(self):
        items, was_trunc = truncate_list([1, 2, 3, 4, 5], max_items=2)
        assert items == [1, 2]
        assert was_trunc is True

    def test_equal_to_limit_is_not_truncated(self):
        items, was_trunc = truncate_list([1, 2], max_items=2)
        assert items == [1, 2]
        assert was_trunc is False

    def test_empty_input(self):
        items, was_trunc = truncate_list([], max_items=10)
        assert items == []
        assert was_trunc is False


# ---------------------------------------------------------------------------
# paginate_list — Phase 4d primitive
# ---------------------------------------------------------------------------


class TestPaginateList:
    """Contract for the pagination primitive used by all O(N) MCP tools.

    Tests are exhaustive around boundaries (negative start, past-end
    start, zero limit, None limit) because subtle off-by-one bugs here
    would silently degrade every downstream tool.
    """

    def test_default_returns_everything(self):
        sl, meta = paginate_list([1, 2, 3])
        assert sl == [1, 2, 3]
        assert meta == {
            "total": 3,
            "start_index": 0,
            "limit": -1,
            "returned": 3,
            "has_more": False,
        }

    def test_limit_zero_returns_empty(self):
        sl, meta = paginate_list([1, 2, 3], limit=0)
        assert sl == []
        assert meta["returned"] == 0
        # has_more must be True since items remain after the (empty) slice.
        assert meta["has_more"] is True

    def test_limit_negative_returns_empty(self):
        sl, meta = paginate_list([1, 2, 3], limit=-1)
        assert sl == []
        assert meta["limit"] == -1
        # We accepted the negative limit; meta echoes it as-is so the
        # caller can detect a misuse.  Spec: empty slice.

    def test_limit_caps_returned_count(self):
        sl, meta = paginate_list([10, 20, 30, 40, 50], limit=2)
        assert sl == [10, 20]
        assert meta["returned"] == 2
        assert meta["has_more"] is True

    def test_start_index_offsets_slice(self):
        sl, meta = paginate_list([10, 20, 30, 40, 50], start_index=2)
        assert sl == [30, 40, 50]
        assert meta["start_index"] == 2
        assert meta["has_more"] is False

    def test_negative_start_index_clamped_to_zero(self):
        sl, meta = paginate_list([1, 2, 3], start_index=-5, limit=2)
        assert sl == [1, 2]
        assert meta["start_index"] == 0

    def test_past_end_start_returns_empty_no_more(self):
        sl, meta = paginate_list([1, 2, 3], start_index=10, limit=5)
        assert sl == []
        assert meta["has_more"] is False
        assert meta["total"] == 3

    def test_start_plus_limit_exactly_at_end_no_more(self):
        sl, meta = paginate_list([1, 2, 3, 4, 5], start_index=3, limit=2)
        assert sl == [4, 5]
        assert meta["returned"] == 2
        assert meta["has_more"] is False

    def test_start_plus_limit_one_short_of_end_signals_more(self):
        sl, meta = paginate_list([1, 2, 3, 4, 5], start_index=2, limit=2)
        assert sl == [3, 4]
        assert meta["has_more"] is True

    def test_empty_input_with_any_args(self):
        sl, meta = paginate_list([], start_index=5, limit=10)
        assert sl == []
        assert meta == {
            "total": 0,
            "start_index": 5,
            "limit": 10,
            "returned": 0,
            "has_more": False,
        }

    def test_meta_total_matches_input_length(self):
        for n in (0, 1, 7, 100):
            sl, meta = paginate_list(list(range(n)), limit=3)
            assert meta["total"] == n

    def test_returns_new_list_not_alias(self):
        """Slicing must return a new list — callers may mutate the
        result without affecting the input."""
        src = [1, 2, 3, 4, 5]
        sl, _ = paginate_list(src, start_index=1, limit=2)
        sl.append(99)
        assert src == [1, 2, 3, 4, 5]
