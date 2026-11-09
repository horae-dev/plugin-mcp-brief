"""Link trust and text escaping of the note section."""

from __future__ import annotations

import pytest

from mcp_brief import render

PREFIX = ["https://finance.example.test/app/"]


@pytest.mark.parametrize(
    ("url", "trusted"),
    [
        ("https://finance.example.test/app/x", True),
        ("https://FINANCE.example.test/app/x", True),
        ("https://finance.example.test.:443/app/x", True),
        ("https://finance.example.test/app/", True),
        ("https://finance.example.test/App/x", False),  # the path is case-sensitive
        ("https://finance.example.test/apple", False),
        ("https://finance.example.test:8443/app/x", False),
        ("https://finance.example.test/app/../x", False),
        ("https://finance.example.test/app/%2e%2E/x", False),
        ("https://finance.example.test/app/./x", False),
        ("https://sub.finance.example.test/app/x", False),
        ("https://finance.example.test.evil.test/app/x", False),
        ("http://finance.example.test/app/x", False),
        ("https://finance.example.test:99999/app/x", False),
        ("https://finance.example.test:0/app/x", False),
    ],
)
def test_a_url_is_trusted_only_under_a_prefix(url: str, trusted: bool) -> None:
    assert render.under(url, PREFIX) is trusted


def test_no_prefix_trusts_nothing() -> None:
    assert not render.under("https://finance.example.test/app/x", [])


def test_data_defuses_block_markers_even_when_the_sdk_would_not() -> None:
    assert "horae:begin" not in render.data("horae:begin x HORAE:END y")


def test_an_empty_or_unprintable_text_never_raises() -> None:
    assert render.data("​") != ""
