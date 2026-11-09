"""The published schema, the pydantic model and the examples all agree."""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from mcp_brief import SCHEMA_ID, Brief, brief_json_schema

ROOT = Path(__file__).resolve().parent.parent
SCHEMA_FILE = ROOT / "schemas" / "brief" / "v1.json"
EXAMPLES = ROOT / "schemas" / "brief" / "examples"
VALID = sorted((EXAMPLES / "valid").glob("*.json"))
INVALID = sorted((EXAMPLES / "invalid").glob("*.json"))


def _load(path: Path) -> Any:
    return json.loads(path.read_text())


@pytest.fixture(scope="module")
def validator() -> Draft202012Validator:
    schema = _load(SCHEMA_FILE)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)


def test_the_published_schema_is_the_model_export() -> None:
    assert _load(SCHEMA_FILE) == brief_json_schema(), (
        "run `uv run python scripts/export_schema.py` and commit the result"
    )


def test_the_schema_is_versioned_and_strict() -> None:
    schema = _load(SCHEMA_FILE)
    assert schema["$id"] == SCHEMA_ID
    assert SCHEMA_ID.endswith("/schemas/brief/v1.json")
    assert schema["properties"]["schema_version"]["const"] == 1
    assert schema["additionalProperties"] is False
    for name, sub in schema["$defs"].items():
        assert sub["additionalProperties"] is False, name


def test_counts_and_lengths_are_capped() -> None:
    schema = _load(SCHEMA_FILE)
    props = schema["properties"]
    assert {k: props[k]["maxItems"] for k in ("alerts", "tasks", "links")} == {
        "alerts": 10,
        "tasks": 10,
        "links": 5,
    }
    assert props["headline"]["maxLength"] == 200


def test_there_are_examples_of_both_kinds() -> None:
    assert len(VALID) >= 3
    assert len(INVALID) >= 10


@pytest.mark.parametrize("path", VALID, ids=lambda p: p.stem)
def test_valid_examples(path: Path, validator: Draft202012Validator) -> None:
    validator.validate(_load(path))
    Brief.model_validate(_load(path))


@pytest.mark.parametrize("path", INVALID, ids=lambda p: p.stem)
def test_invalid_examples(path: Path, validator: Draft202012Validator) -> None:
    assert not validator.is_valid(_load(path))
    with pytest.raises(ValidationError):
        Brief.model_validate(_load(path))


def test_the_example_service_returns_a_valid_brief(validator: Draft202012Validator) -> None:
    spec = importlib.util.spec_from_file_location(
        "finance_coach", ROOT / "examples/finance_coach.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    document = module.brief()
    validator.validate(document)
    Brief.model_validate(document)


def test_the_docs_embed_the_example_service() -> None:
    docs = (ROOT / "docs" / "brief-contract.md").read_text()
    match = re.search(r"<!-- example-service -->\n```python\n(.*?)```\n", docs, re.S)
    assert match, "docs/brief-contract.md must embed examples/finance_coach.py"
    source = (ROOT / "examples" / "finance_coach.py").read_text()
    assert match.group(1) == source
    assert len(source.splitlines()) <= 50


@pytest.mark.parametrize(
    "patch",
    [
        {"generated_at": "2026-13-10T06:30:00Z"},
        {"valid_until": "2026-11-10T06:00:00Z"},
        {"tasks": [{"id": "a", "title": "t", "due": "2026-02-31"}]},
    ],
    ids=["month-13", "window-reversed", "impossible-date"],
)
def test_the_model_also_checks_what_a_pattern_cannot(patch: dict[str, Any]) -> None:
    document = {**_load(EXAMPLES / "valid" / "minimal.json"), **patch}
    with pytest.raises(ValidationError):
        Brief.model_validate(document)


HOSTILE = {
    "bidi-override": "safe \u202etxt.exe",
    "zero-width-space": "pay\u200bnow",
    "line-separator": "one\u2028two",
    "paragraph-separator": "one\u2029two",
    "c1-control": "one\u0085two",
    "bom": "\ufeffhi",
    "tag-character": "hi\U000e0041",
}


@pytest.mark.parametrize("text", HOSTILE.values(), ids=HOSTILE.keys())
def test_invisible_characters_are_refused_everywhere(
    text: str, validator: Draft202012Validator
) -> None:
    base = _load(EXAMPLES / "valid" / "minimal.json")
    for document in (
        {**base, "headline": text},
        {**base, "service": {"id": "x", "name": text}},
        {**base, "status": {"state": "degraded", "reason": text}},
        {**base, "alerts": [{"key": "a", "severity": "warn", "title": text}]},
        {**base, "tasks": [{"id": "a", "title": text}]},
        {**base, "links": [{"label": text, "url": "https://example.test/"}]},
    ):
        with pytest.raises(ValidationError):
            Brief.model_validate(document)
        if text != HOSTILE["tag-character"]:  # beyond the BMP: the model alone catches it
            assert not validator.is_valid(document)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.test/caf\u00e9",
        "https://exämple.test/",
        "https://user@example.test/",
        "https://example.test/a\\b",
        "https://example.test/a b",
        "https://example.test/\u202e",
        "https://example.test\u2028/",
    ],
)
def test_urls_are_printable_ascii(url: str, validator: Draft202012Validator) -> None:
    document = {**_load(EXAMPLES / "valid" / "minimal.json"), "links": [{"label": "l", "url": url}]}
    assert not validator.is_valid(document)
    with pytest.raises(ValidationError):
        Brief.model_validate(document)


def test_punycode_hosts_and_percent_encoded_paths_pass(validator: Draft202012Validator) -> None:
    url = "https://xn--exmple-cua.test:8443/caf%C3%A9?q=1#top"
    document = {**_load(EXAMPLES / "valid" / "minimal.json"), "links": [{"label": "l", "url": url}]}
    validator.validate(document)
    Brief.model_validate(document)
