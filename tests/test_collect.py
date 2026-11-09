"""`mcp-brief.collect` against in-process services built from the example service."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any

import pytest

from conftest import Run, service
from helpers import BODY_MARKER, TOKEN, Variant, example_brief, stamp
from mcp_brief import build, render

FINANCE = "finance.example.test"
HEALTH = "health.example.test"


def rows(result: Any) -> dict[str, Any]:
    return {row["id"]: row for row in result.data["services"]}


def of_type(result: Any, kind: str) -> list[dict[str, Any]]:
    return [intent for intent in result.intents if intent["type"] == kind]


def test_one_service_feeds_a_section_a_task_and_no_push(run: Run) -> None:
    result = run({FINANCE: Variant()}, [service(FINANCE, id="finance-coach")])
    assert result.error is None
    assert result.data == {
        "complete": True,
        "services": [
            {
                "id": "finance-coach",
                "status": "ok",
                "reason": None,
                "brief_state": "ok",
                "tasks": 1,
                "pushes": 0,
                "dropped_tasks": 0,
                "dropped_pushes": 0,
            }
        ],
    }
    note, task = result.intents
    assert note == {
        "type": "note.replace_owned_block",
        "block": "brief-finance-coach",
        "content": (
            "### Finance\n\n"
            "**Summary:** Spending is 8\\% under budget this month; one bill is due Friday.\n\n"
            "- **Warn:** Groceries at 82\\% of the monthly budget\n\n"
            "**Links:** Monthly overview\n"
        ),
    }
    assert task == {
        "type": "task.create",
        "title": "Pay the electricity bill",
        "fingerprint": "finance-coach:bill.electricity.2026-11",
        "due": "2026-11-14",
    }
    assert "path" not in note  # the core fills it from the `act` entry


def test_a_link_is_a_link_only_under_a_trusted_prefix(run: Run) -> None:
    brief = example_brief(
        links=[
            {"label": "Trusted", "url": "https://finance.example.test/app/overview"},
            {"label": "Other", "url": "https://elsewhere.example.test/overview"},
        ]
    )
    prefixes = ["https://finance.example.test/app/"]
    result = run({FINANCE: Variant(brief)}, [service(FINANCE, link_prefix=prefixes)])
    content = of_type(result, "note.replace_owned_block")[0]["content"]
    assert "**Links:** [Trusted](https://finance.example.test/app/overview) | Other\n" in content
    assert "elsewhere" not in json.dumps(result.intents)


def test_two_services_flow_together(run: Run) -> None:
    health = example_brief(
        service={"id": "health", "name": "Health"}, headline="Load is moderate.", tasks=[]
    )
    result = run(
        {FINANCE: Variant(), HEALTH: Variant(health)},
        [service(FINANCE), service(HEALTH)],
    )
    assert result.data["complete"] is True
    assert [row["id"] for row in result.data["services"]] == ["finance", "health"]
    blocks = [i["block"] for i in of_type(result, "note.replace_owned_block")]
    assert blocks == ["brief-finance", "brief-health"]
    assert [t["fingerprint"] for t in of_type(result, "task.create")] == [
        "finance:bill.electricity.2026-11"
    ]


# --- failures: degraded with a fixed reason, the others still flow ---------------------------

slow = Variant(delay=5)
FAILURES: list[tuple[str, Variant, str]] = [
    ("timeout", slow, "timeout"),
    ("unauthorized", Variant(token="another-token-9876543210"), "auth_failed"),  # noqa: S106
    ("forbidden", Variant(status=403), "auth_failed"),
    ("server_error", Variant(status=500), "http_error"),
    ("not_found", Variant(status=404), "http_error"),
    ("garbage_text", Variant(text="<<< not json " + BODY_MARKER), "invalid_json"),
    ("garbage_body", Variant(raw=b"{" + BODY_MARKER.encode()), "protocol_error"),
    ("tool_error", Variant(fail=True), "tool_error"),
    ("oversize", Variant(example_brief(padding="x" * 100_000)), "oversize"),
    ("invalid_schema", Variant(example_brief(surprise=BODY_MARKER)), "schema_violation"),
    ("bad_version", Variant(example_brief(schema_version=2)), "schema_violation"),
    (
        "duplicate_task",
        Variant(example_brief(tasks=[{"id": "a", "title": "x"}, {"id": "a", "title": "y"}])),
        "schema_violation",
    ),
    (
        "duplicate_alert",
        Variant(
            example_brief(
                alerts=[
                    {"key": "k", "severity": "crit", "title": "x"},
                    {"key": "k", "severity": "crit", "title": "y"},
                ]
            )
        ),
        "schema_violation",
    ),
    (
        "from_the_future",
        Variant(example_brief(generated_at=stamp(dt.timedelta(minutes=6)))),
        "clock_skew",
    ),
    (
        "stale",
        Variant(example_brief(valid_until=stamp(dt.timedelta(seconds=-1)),
                              generated_at=stamp(dt.timedelta(hours=-2)))),
        "stale",
    ),
    ("redirect_elsewhere", Variant(redirect="https://evil.example.test/mcp"), "redirect_refused"),
    ("offline", Variant(), "connection_failed"),
]  # fmt: skip


@pytest.mark.parametrize(("name", "variant", "reason"), FAILURES, ids=[f[0] for f in FAILURES])
def test_a_failure_degrades_that_service_and_never_the_step(
    run: Run, name: str, variant: Variant, reason: str
) -> None:
    sick = "sick.example.test"
    served = {FINANCE: Variant(), sick: variant}
    if name == "offline":
        del served[sick]
    result = run(
        served,
        [service(FINANCE), service(sick, token_secret="MCP_BRIEF_TOKEN_1", timeout=1)],  # noqa: S106
        network=[FINANCE, sick],
    )
    assert result.error is None
    rest = rows(result)
    assert rest["sick"]["status"] == "degraded"
    assert rest["sick"]["reason"] == reason
    assert rest["finance"]["status"] == "ok"
    assert result.data["complete"] is False
    # The healthy service still gets its section and task.
    assert of_type(result, "note.replace_owned_block")[0]["block"] == "brief-finance"
    assert any(t["fingerprint"].startswith("finance:") for t in of_type(result, "task.create"))
    # The sick one: no task, no push, and a section that says it is unavailable (or stale).
    assert not [t for t in of_type(result, "task.create") if t["fingerprint"].startswith("sick:")]
    assert of_type(result, "notify.push") == []
    sick_block = [
        i for i in of_type(result, "note.replace_owned_block") if i["block"] == "brief-sick"
    ]
    assert len(sick_block) == 1
    text = json.dumps([result.data, result.intents])
    # Nothing from the response and never the token.
    assert BODY_MARKER not in text
    assert TOKEN not in text
    assert "another-token" not in text
    if reason != "stale":
        assert sick_block[0]["content"] == f"### sick\n\n**Unavailable:** {reason}\n"


def test_an_unauthorized_request_carried_the_token_and_nothing_leaks(run: Run) -> None:
    variant = Variant(token="the-right-token-0000000000")  # noqa: S106
    result = run({FINANCE: variant}, [service(FINANCE, token_secret="MCP_BRIEF_TOKEN_1")])  # noqa: S106
    assert rows(result)["finance"]["reason"] == "auth_failed"
    assert variant.seen
    assert all(h["authorization"] == f"Bearer {TOKEN}" for h in variant.seen)
    assert TOKEN not in json.dumps([result.data, result.intents])


def test_the_token_is_sent_only_when_the_service_names_a_secret(run: Run) -> None:
    open_service = Variant()
    run({FINANCE: open_service}, [service(FINANCE)])
    assert open_service.seen
    assert all("authorization" not in h for h in open_service.seen)
    secured = Variant(token=TOKEN)
    result = run({FINANCE: secured}, [service(FINANCE, token_secret="MCP_BRIEF_TOKEN_1")])  # noqa: S106
    assert rows(result)["finance"]["status"] == "ok"


@pytest.mark.parametrize(
    ("environ", "reason"),
    [({}, "secret_missing"), ({"MCP_BRIEF_TOKEN_1": ""}, "secret_missing"),
     ({"MCP_BRIEF_TOKEN_1": "has space in it"}, "secret_invalid"),
     ({"MCP_BRIEF_TOKEN_1": "line\nbreak-token"}, "secret_invalid")],
)  # fmt: skip
def test_a_missing_or_unusable_token_degrades_without_contacting_the_service(
    run: Run, environ: dict[str, str], reason: str
) -> None:
    variant = Variant()
    result = run(
        {FINANCE: variant},
        [service(FINANCE, token_secret="MCP_BRIEF_TOKEN_1")],  # noqa: S106
        environ=environ,
    )
    assert rows(result)["finance"]["reason"] == reason
    assert variant.seen == []
    assert "has space" not in json.dumps(result.data)


def test_a_redirect_to_another_host_is_never_followed(run: Run) -> None:
    evil = Variant()
    start = Variant(redirect="https://evil.example.test/mcp", token=TOKEN)
    result = run(
        {FINANCE: start, "evil.example.test": evil},
        [service(FINANCE, token_secret="MCP_BRIEF_TOKEN_1")],  # noqa: S106
        network=[FINANCE, "evil.example.test"],
    )
    assert rows(result)["finance"]["reason"] == "redirect_refused"
    assert evil.seen == []  # the credentialed request never reached it


def test_a_redirect_loop_within_the_origin_ends_in_a_refusal_not_a_hang(run: Run) -> None:
    loop = Variant(redirect="https://finance.example.test/mcp")
    result = run({FINANCE: loop}, [service(FINANCE, timeout=5)])
    assert rows(result)["finance"]["status"] == "degraded"
    assert rows(result)["finance"]["reason"] in {"redirect_refused", "http_error"}


def test_a_bug_in_one_service_does_not_cost_the_others() -> None:
    import anyio
    from horae_sdk.testing import call

    from conftest import TOOL
    from helpers import NOW, serve
    from mcp_brief import build

    async def go() -> Any:
        async with serve({FINANCE: Variant(), HEALTH: Variant()}) as ok:

            def transport(svc: Any) -> Any:
                if svc.id == "health":
                    raise RuntimeError(BODY_MARKER)
                return ok(svc)

            plugin = build(transport, environ={}, clock=lambda: NOW)
            return await call(
                plugin,
                TOOL,
                {"services": [service(FINANCE), service(HEALTH)]},
                network=[FINANCE, HEALTH],
            )

    result = anyio.run(go)
    assert {k: v["reason"] for k, v in rows(result).items()} == {
        "finance": None,
        "health": "request_failed",
    }
    assert BODY_MARKER not in json.dumps(result.data)


def test_a_link_the_sdk_refuses_costs_the_link_not_the_item(
    run: Run, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(render, "under", lambda *_: True)  # as if an IP host were trusted
    link = "https://127.0.0.1/x"
    brief = example_brief(
        tasks=[{"id": "a", "title": "A", "link": link}],
    )
    result = run({FINANCE: Variant(brief)}, [service(FINANCE)])
    assert "link" not in of_type(result, "task.create")[0]


def test_a_link_with_an_impossible_port_does_not_break_the_service(run: Run) -> None:
    link = "https://finance.example.test:99999/x"
    brief = example_brief(tasks=[{"id": "a", "title": "A", "link": link}])
    prefix = ["https://finance.example.test/"]
    result = run({FINANCE: Variant(brief)}, [service(FINANCE, link_prefix=prefix)])
    assert rows(result)["finance"]["status"] == "ok"
    assert "link" not in of_type(result, "task.create")[0]


# --- the contract's rules ---------------------------------------------------------------------


def test_only_crit_alerts_push_with_a_stable_key(run: Run) -> None:
    alerts = [
        {"key": "balance.low", "severity": "crit", "title": "Checking account is low"},
        {"key": "budget.80", "severity": "warn", "title": "Groceries at 82 percent"},
        {"key": "fyi", "severity": "info", "title": "Just so you know"},
    ]
    result = run({FINANCE: Variant(example_brief(alerts=alerts))}, [service(FINANCE, id="fin")])
    assert of_type(result, "notify.push") == [
        {
            "type": "notify.push",
            "body": "Checking account is low",
            "title": "Finance",
            "key": "fin:balance.low",
        }
    ]
    content = of_type(result, "note.replace_owned_block")[0]["content"]
    assert content.count("Checking account is low") == 1  # a crit alert is in the section once
    assert "**Warn:** Groceries at 82 percent" in content
    assert "**Info:** Just so you know" in content
    assert rows(result)["fin"]["pushes"] == 1


def test_a_push_link_follows_the_trusted_prefix(run: Run) -> None:
    alert = {
        "key": "k",
        "severity": "crit",
        "title": "Look",
        "link": "https://finance.example.test/alerts/1",
    }
    variants = {FINANCE: Variant(example_brief(alerts=[alert]))}
    on = run(variants, [service(FINANCE, link_prefix=["https://finance.example.test/alerts/"])])
    assert of_type(on, "notify.push")[0]["link"] == "https://finance.example.test/alerts/1"
    off = run(variants, [service(FINANCE)])
    assert "link" not in of_type(off, "notify.push")[0]
    assert (
        "finance.example.test/alerts" not in of_type(off, "note.replace_owned_block")[0]["content"]
    )


def test_a_trusted_link_label_cannot_open_a_second_link(run: Run) -> None:
    brief = example_brief(
        links=[
            {"label": "[evil](https://evil.example.test)", "url": "https://finance.example.test/a"}
        ]
    )
    prefix = ["https://finance.example.test/"]
    result = run({FINANCE: Variant(brief)}, [service(FINANCE, link_prefix=prefix)])
    content = of_type(result, "note.replace_owned_block")[0]["content"]
    assert (
        "**Links:** [\\[evil\\]\\(https\\://evil.example.test\\)](https://finance.example.test/a)"
        in content
    )


def test_task_links_are_kept_only_under_a_prefix_and_never_by_dot_segments(run: Run) -> None:
    tasks = [
        {"id": "a", "title": "A", "link": "https://finance.example.test/t/1"},
        {"id": "b", "title": "B", "link": "https://finance.example.test/t/../secret"},
        {"id": "c", "title": "C", "link": "https://other.example.test/t/1"},
        {"id": "d", "title": "D", "link": "https://127.0.0.1/t/1"},
        {"id": "e", "title": "E", "due": "2026-12-01"},
    ]
    result = run(
        {FINANCE: Variant(example_brief(tasks=tasks))},
        [service(FINANCE, id="f", link_prefix=["https://finance.example.test/t/"])],
    )
    created = {t["fingerprint"]: t for t in of_type(result, "task.create")}
    assert set(created) == {"f:a", "f:b", "f:c", "f:d", "f:e"}
    assert created["f:a"]["link"] == "https://finance.example.test/t/1"
    assert [k for k, t in created.items() if "link" in t] == ["f:a"]
    assert created["f:e"]["due"] == "2026-12-01"


def test_a_stale_brief_gives_no_task_and_no_push(run: Run) -> None:
    alert = {"key": "k", "severity": "crit", "title": "Urgent"}
    brief = example_brief(
        alerts=[alert],
        generated_at=stamp(dt.timedelta(hours=-3)),
        valid_until=stamp(dt.timedelta(hours=-1)),
    )
    result = run({FINANCE: Variant(brief)}, [service(FINANCE)])
    row = rows(result)["finance"]
    assert (row["status"], row["reason"], row["brief_state"]) == ("degraded", "stale", "ok")
    assert [i["type"] for i in result.intents] == ["note.replace_owned_block"]
    content = result.intents[0]["content"]
    assert content.startswith("### Finance\n\n**Stale:** this brief expired at ")
    assert "Last headline: Spending is 8\\% under budget" in content
    assert "Urgent" not in content


def test_the_boundary_of_validity_is_not_stale(run: Run) -> None:
    brief = example_brief(generated_at=stamp(dt.timedelta(hours=-1)), valid_until=stamp())
    result = run({FINANCE: Variant(brief)}, [service(FINANCE)])
    assert rows(result)["finance"]["status"] == "ok"


def test_an_error_brief_shows_its_reason_and_ignores_its_items(run: Run) -> None:
    brief = example_brief(
        status={"state": "error", "reason": "database is down"},
        alerts=[{"key": "k", "severity": "crit", "title": "Pushy"}],
    )
    result = run({FINANCE: Variant(brief)}, [service(FINANCE)])
    row = rows(result)["finance"]
    assert (row["status"], row["reason"], row["brief_state"]) == (
        "degraded",
        "service_error",
        "error",
    )
    assert [i["type"] for i in result.intents] == ["note.replace_owned_block"]
    content = result.intents[0]["content"]
    assert "**Status:** error - database is down" in content
    assert "Pushy" not in content
    assert of_type(result, "task.create") == []


def test_a_degraded_brief_is_still_used_and_says_why(run: Run) -> None:
    brief = example_brief(status={"state": "degraded", "reason": "bank feed is a day old"})
    result = run({FINANCE: Variant(brief)}, [service(FINANCE)])
    row = rows(result)["finance"]
    assert (row["status"], row["brief_state"]) == ("ok", "degraded")
    assert "**Status:** degraded - bank feed is a day old" in result.intents[0]["content"]
    assert len(of_type(result, "task.create")) == 1


def test_the_response_cap_is_configurable(run: Run) -> None:
    served = {FINANCE: Variant()}
    assert rows(run(served, [service(FINANCE)]))["finance"]["status"] == "ok"
    small = run(served, [service(FINANCE)], max_response_bytes=1024)
    assert rows(small)["finance"]["reason"] == "oversize"
    big = Variant(example_brief(headline="h" * 100, padding="p" * 70_000))
    assert rows(run({FINANCE: big}, [service(FINANCE)]))["finance"]["reason"] == "oversize"
    assert (
        rows(run({FINANCE: big}, [service(FINANCE)], max_response_bytes=512 * 1024))["finance"][
            "reason"
        ]
        == "schema_violation"
    )  # under the cap now, and the padding is not in the contract


def test_the_clock_skew_is_configurable(run: Run) -> None:
    brief = example_brief(generated_at=stamp(dt.timedelta(minutes=30)),
                          valid_until=stamp(dt.timedelta(hours=12)))  # fmt: skip
    served = {FINANCE: Variant(brief)}
    assert rows(run(served, [service(FINANCE)]))["finance"]["reason"] == "clock_skew"
    assert rows(run(served, [service(FINANCE)], max_skew_seconds=3600))["finance"]["status"] == "ok"
    edge = Variant(example_brief(generated_at=stamp(dt.timedelta(seconds=300))))
    assert rows(run({FINANCE: edge}, [service(FINANCE)]))["finance"]["status"] == "ok"


# --- untrusted text ---------------------------------------------------------------------------


def test_hostile_text_is_escaped_in_the_section(run: Run) -> None:
    markup = (
        "[x](javascript:alert(1)) ![i](https://evil.example.test/p.png) <%tp.file%> `dv` $=1 #tag"
    )
    tricks = "📅 2020-01-01 🔁 every day horae:begin brief-x <!-- horae:end --> HORAE:BEGIN [[w]]"
    brief = example_brief(
        service={"id": "finance-coach", "name": "# Name ## horae:end"},
        headline=markup + " " + tricks,
        alerts=[{"key": "k", "severity": "warn", "title": tricks, "body": "- [ ] do it"}],
        status={"state": "degraded", "reason": "> quote"},
        links=[{"label": "[evil](https://evil.example.test)", "url": "https://evil.example.test/"}],
    )
    result = run({FINANCE: Variant(brief)}, [service(FINANCE)])
    assert rows(result)["finance"]["status"] == "ok"  # the brief was valid, so this is its text
    content = of_type(result, "note.replace_owned_block")[0]["content"]
    assert not re.search(r"(?i)horae:(begin|end)", content)
    for line in content.splitlines():
        # Every line starts with the plugin's own fixed syntax; untrusted text never starts one.
        assert line == "" or line.startswith(("### ", "**", "- **")), line
    for active in ("](javascript", "[x]", "<%", "📅", "🔁", "[[", "\n- [ ]", "<!--", "![", "`dv`"):
        assert active not in content, active
    assert "](https://evil.example.test)" not in content  # the label cannot make its own link
    assert "**Links:** \\[evil\\]" in content  # no prefix: the label alone, escaped


# --- hosts ------------------------------------------------------------------------------------


def test_a_host_the_lock_does_not_approve_is_never_contacted(run: Run) -> None:
    variant = Variant()
    for network in ([], ["other.example.test"]):
        result = run({FINANCE: variant}, [service(FINANCE)], network=network)
        assert rows(result)["finance"]["reason"] == "host_not_approved"
        assert result.error is None
    assert variant.seen == []


def test_an_older_core_falls_back_to_the_declared_and_allowed_hosts(run: Run) -> None:
    variant = Variant()
    refused = run({FINANCE: variant}, [service(FINANCE)], network=None)
    assert rows(refused)["finance"]["reason"] == "host_not_approved"
    assert variant.seen == []
    allowed = run({FINANCE: variant}, [service(FINANCE)], network=None, allowed_hosts=[FINANCE])
    assert rows(allowed)["finance"]["status"] == "ok"
    assert variant.seen


def test_one_unapproved_host_does_not_stop_the_others(run: Run) -> None:
    result = run(
        {FINANCE: Variant(), HEALTH: Variant(example_brief(service={"id": "h", "name": "H"}))},
        [service(FINANCE), service(HEALTH)],
        network=[HEALTH],
    )
    assert {k: v["reason"] for k, v in rows(result).items()} == {
        "finance": "host_not_approved",
        "health": None,
    }


# --- configuration ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"url": "http://finance.example.test/mcp"},
        {"url": "https://user:pw@finance.example.test/mcp"},
        {"url": "https://finance.example.test/mcp?token=abc"},
        {"url": "https://127.0.0.1/mcp"},
        {"url": "https://finance.example.test/mcp#frag"},
        {"url": "https://finance.example.test/a b"},
        {"timeout": 0},
        {"timeout": 61},
        {"token_secret": "PATH"},
        {"token_secret": "mcp_brief_token_1"},
        {"link_prefix": ["https://finance.example.test/no-trailing-slash"]},
        {"link_prefix": ["http://finance.example.test/"]},
        {"link_prefix": ["https://finance.example.test/a/../"]},
        {"surprise": True},
    ],
)
def test_a_bad_service_entry_is_degraded_not_fatal(run: Run, fields: dict[str, Any]) -> None:
    good = Variant(example_brief(service={"id": "h", "name": "H"}))
    bad = {**service(FINANCE), **fields}
    result = run({FINANCE: Variant(), HEALTH: good}, [bad, service(HEALTH)])
    assert result.error is None
    assert rows(result)["finance"]["reason"] == "config_invalid"
    assert rows(result)["health"]["status"] == "ok"


@pytest.mark.parametrize(
    "services",
    [
        [],
        "nope",
        [{"url": "https://finance.example.test/mcp"}],
        [{"id": "Bad_Id", "url": "https://finance.example.test/mcp"}],
        [service(FINANCE), service(FINANCE)],
        ["not a mapping"],
        [service(f"h{n}.example.test") for n in range(9)],
    ],
)
def test_a_list_that_cannot_be_told_apart_is_a_tool_error(run: Run, services: Any) -> None:
    result = run({}, services)
    assert result.error is not None
    assert result.error.startswith("config_invalid") or "services" in result.error


@pytest.mark.parametrize(
    "params",
    [
        {"max_response_bytes": 10},
        {"max_response_bytes": 10**9},
        {"max_skew_seconds": -1},
        {"max_skew_seconds": 10**6},
        {"allowed_hosts": ["Bad.Example"]},
        {"allowed_hosts": ["localhost"]},
        {"allowed_hosts": ["10.0.0.1"]},
        {"allowed_hosts": [f"h{n}.example.test" for n in range(17)]},
    ],
)
def test_bad_limits_are_a_tool_error(run: Run, params: dict[str, Any]) -> None:
    result = run({FINANCE: Variant()}, [service(FINANCE)], **params)
    assert result.error is not None
    assert "config_invalid" in result.error


def test_the_service_id_comes_from_the_configuration_not_the_service(run: Run) -> None:
    brief = example_brief(service={"id": "evil", "name": "Evil"})
    result = run({FINANCE: Variant(brief)}, [service(FINANCE, id="mine")])
    assert result.intents[0]["block"] == "brief-mine"
    assert result.intents[1]["fingerprint"].startswith("mine:")


def test_intents_are_deterministic(run: Run) -> None:
    served = {FINANCE: Variant(), HEALTH: Variant(example_brief(service={"id": "h", "name": "H"}))}
    first = run(served, [service(FINANCE), service(HEALTH)])
    second = run(served, [service(FINANCE), service(HEALTH)])
    assert first.data == second.data
    assert first.intents == second.intents


def test_a_full_brief_stays_within_the_core_limits(run: Run) -> None:
    many = example_brief(
        alerts=[{"key": f"k{n}", "severity": "crit", "title": f"A{n}"} for n in range(10)],
        tasks=[{"id": f"t{n}", "title": f"T{n}"} for n in range(10)],
    )
    served = {f"s{n}.example.test": Variant(many) for n in range(8)}
    result = run(served, [service(host) for host in served])
    assert len(result.intents) == 8 * (1 + 10 + 3) < 256


# --- hardening (#7) ---------------------------------------------------------------------------


def test_a_push_title_and_body_are_escaped_like_note_text(run: Run) -> None:
    alert = {"key": "k", "severity": "crit", "title": "See [[Secret]] #tag [x](https://a.test)"}
    brief = example_brief(service={"id": "f", "name": "Fin [[Vault]] #tag"}, alerts=[alert])
    push = of_type(run({FINANCE: Variant(brief)}, [service(FINANCE)]), "notify.push")[0]
    assert push["body"] == render.data(alert["title"])
    assert push["title"] == render.data("Fin [[Vault]] #tag")
    for text in (push["body"], push["title"]):
        assert "[[" not in text
        assert "](" not in text


def test_pushes_and_tasks_are_capped_per_service_and_counted(run: Run) -> None:
    many = example_brief(
        alerts=[{"key": f"k{n}", "severity": "crit", "title": f"Secret {n}"} for n in range(7)],
        tasks=[{"id": f"t{n}", "title": f"Task {n}"} for n in range(10)],
    )
    served = {FINANCE: Variant(many)}
    result = run(served, [service(FINANCE)], max_tasks_per_service=4)
    row = rows(result)["finance"]
    assert (row["pushes"], row["dropped_pushes"]) == (3, 4)  # default: 3 pushes
    assert (row["tasks"], row["dropped_tasks"]) == (4, 6)
    assert [i["key"] for i in of_type(result, "notify.push")] == [f"finance:k{n}" for n in range(3)]
    assert len(of_type(result, "task.create")) == 4
    assert "Secret 5" not in json.dumps(result.data)  # counts only, no text
    wide = run(served, [service(FINANCE)], max_pushes_per_service=7)
    assert rows(wide)["finance"]["dropped_pushes"] == 0
    assert len(of_type(wide, "notify.push")) == 7
    assert rows(wide)["finance"]["dropped_tasks"] == 0  # default: 10 tasks


@pytest.mark.parametrize(
    "params",
    [
        {"max_pushes_per_service": 0},
        {"max_pushes_per_service": 11},
        {"max_tasks_per_service": 0},
        {"max_tasks_per_service": 11},
        {"max_validity_hours": 0},
        {"max_validity_hours": 169},
    ],
)
def test_the_new_limits_are_bounded(run: Run, params: dict[str, int]) -> None:
    result = run({FINANCE: Variant()}, [service(FINANCE)], **params)
    assert result.error is not None
    assert "config_invalid" in str(result.error)


def test_valid_until_is_bounded_after_generated_at(run: Run) -> None:
    def served(hours: float) -> dict[str, Variant]:
        return {FINANCE: Variant(example_brief(valid_until=stamp(dt.timedelta(hours=hours))))}

    edge = rows(run(served(48), [service(FINANCE)]))["finance"]
    assert edge["status"] == "ok"  # exactly 48 h is allowed
    over = run(served(48.1), [service(FINANCE)])
    assert rows(over)["finance"]["reason"] == "schema_violation"
    assert [i["type"] for i in over.intents] == ["note.replace_owned_block"]
    assert "Spending" not in over.intents[0]["content"]  # nothing from the document
    longer = run(served(100), [service(FINANCE)], max_validity_hours=168)
    assert rows(longer)["finance"]["status"] == "ok"
    shorter = run(served(12.5), [service(FINANCE)], max_validity_hours=12)
    assert rows(shorter)["finance"]["reason"] == "schema_violation"


def test_the_mcp_session_id_logger_is_lowered_to_warning() -> None:
    logger = logging.getLogger("mcp.client.streamable_http")
    logger.setLevel(logging.NOTSET)
    build()
    assert logger.level == logging.WARNING
    assert not logger.isEnabledFor(logging.INFO)
