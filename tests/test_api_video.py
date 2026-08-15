"""HTTP byte-range parsing for the source-video endpoint.

Regression cover for a bug that returned the WRONG BYTES and defeated the chunk
cap: a suffix range (`bytes=-100`, meaning the last 100 bytes) did not match the
old regex, so the handler fell back to start=0 and served the whole object with a
206 and a `Content-Range` claiming it was the requested range.

That form is not exotic. MP4 players use a suffix range to locate a trailing
`moov` atom, which is exactly how a non-faststart console capture is laid out -
so the common case was a multi-GB read into the API process returning data the
player could not use.
"""
from __future__ import annotations

import pytest

from api.routes.matches import VIDEO_CHUNK_BYTES, parse_byte_range

TOTAL = 1_000_000


def test_normal_range():
    assert parse_byte_range("bytes=0-99", TOTAL) == ("ok", 0, 99)


def test_open_ended_range_runs_to_end_of_file():
    kind, start, end = parse_byte_range("bytes=500-", TOTAL)
    assert (kind, start, end) == ("ok", 500, TOTAL - 1)


def test_suffix_range_returns_the_LAST_n_bytes():
    """The bug: this used to resolve to the start of the file."""
    assert parse_byte_range("bytes=-100", TOTAL) == ("ok", TOTAL - 100, TOTAL - 1)


def test_suffix_range_larger_than_file_clamps_to_whole_file():
    kind, start, _end = parse_byte_range(f"bytes=-{TOTAL * 2}", TOTAL)
    assert (kind, start) == ("ok", 0)


@pytest.mark.parametrize("header", ["bytes=1000000-", "bytes=2000000-", "bytes=-0"])
def test_unsatisfiable_ranges_get_416(header):
    assert parse_byte_range(header, TOTAL)[0] == "unsatisfiable"


@pytest.mark.parametrize("header", ["", "bytes=abc", "bytes=", "items=0-10", "bytes=-"])
def test_malformed_range_headers_are_ignored(header):
    """RFC 9110: an unparseable Range header must be ignored (serve 200), not 416."""
    assert parse_byte_range(header, TOTAL)[0] == "ignore"


def test_response_is_capped_to_one_chunk_however_much_is_asked_for():
    """Peak memory must not scale with the size of the uploaded match video."""
    huge = 10 * VIDEO_CHUNK_BYTES
    kind, start, end = parse_byte_range("bytes=0-", huge)
    assert kind == "ok"
    assert end - start + 1 == VIDEO_CHUNK_BYTES


def test_empty_object_never_reports_a_satisfiable_range():
    assert parse_byte_range("bytes=0-10", 0)[0] == "ignore"
