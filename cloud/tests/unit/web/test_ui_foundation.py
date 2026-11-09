"""The redesign's shared vocabulary (docs/impeccable-redesign/BUILD.md): the `you` tone
mapping, the `shortid` and `kindword` filters, the overview's `you` count, the `icon` and
`heal_line` macros, and the shell in base.html. Rendered through the app's own Jinja
environment, with no database."""

import pytest

from romulus_cloud.api.web import _overview, kindword, page_tone, shortid, templates
from romulus_cloud.domain.story import AGENT_OUTCOME_STATUS, STATUS, WORK_ORDER_STATUS


@pytest.mark.parametrize(
    "status",
    [
        STATUS["technician.diagnosed"],
        WORK_ORDER_STATUS["needs_human"],
        WORK_ORDER_STATUS["blocked_over_cap"],
    ],
)
def test_statuses_that_wait_on_a_person_read_as_you(status):
    assert status[1] == "bad"  # the domain keeps its own tone
    assert page_tone(status) == "you"


@pytest.mark.parametrize(
    "status",
    [
        STATUS["agent.run_started"],
        STATUS["overlay.held"],
        STATUS["candidate.rolled_back"],
        STATUS["overlay.superseded"],
        WORK_ORDER_STATUS["stopped"],
        AGENT_OUTCOME_STATUS["fixed"],
        ("open", "bad"),
    ],
)
def test_every_other_status_keeps_its_tone(status):
    assert page_tone(status) == status[1]


@pytest.mark.parametrize(
    ("value", "out"),
    [
        ("wo_3d7cd6464e2e4980a69acd0a3e031264", "wo_3d7cd6…"),
        ("tc_01jcx9e2m4abcdef", "tc_01jcx9…"),
        ("ses_0s1zke8x9q977w", "ses_0s1zke…"),
        ("cand_01jcx9e2m4abcdef", "cand_01jcx9…"),
        ("tc_01jcx9e2m4", "tc_01jcx9e2m4"),  # a 10-character tail is short enough
        ("joint_stall", "joint_stall"),
        ("technician_diagnosed_twice", "technician_diagnosed_twice"),
        ("3f2b8c1e-6a4d-4e7b-9c21-5d8e0f1a2b3c", "3f2b8c1e-6a4d-4e7b-9c21-5d8e0f1a2b3c"),
        ("sim-1 stalled", "sim-1 stalled"),
        ("", ""),
        (None, ""),
        (
            "Naïve technician case tc_a0177e9cb12f4ee394b91511f851a872 opened for work order "
            "wo_3d7cd6464e2e4980a69acd0a3e031264 (needs human)",
            "Naïve technician case tc_a0177e… opened for work order wo_3d7cd6… (needs human)",
        ),
        ("work order wo_3d7c… (needs human)", "work order wo_3d7c… (needs human)"),
        ("joint_stall → work order wo_046dca67f832486aa7ae", "joint_stall → work order wo_046dca…"),
        ("see /technician/tc_b0dcb097068542c7840769a89b9e1e1e", "see /technician/tc_b0dcb0…"),
    ],
)
def test_shortid(value, out):
    assert shortid(value) == out


@pytest.mark.parametrize(
    ("value", "out"),
    [
        ("joint_stall", "Joint stall"),
        ("technician.diagnosed", "Technician diagnosed"),
        ("camera_occluded", "Camera occluded"),
        ("SO101_wrist", "SO101 wrist"),
        ("", ""),
        (None, ""),
    ],
)
def test_kindword(value, out):
    assert kindword(value) == out


def test_shortid_keeps_escaping():
    tpl = templates.env.from_string("{{ t | shortid }}")
    assert tpl.render(t="<b> wo_3d7cd6464e2e4980a6") == "&lt;b&gt; wo_3d7cd6…"


def test_the_filters_are_registered_for_every_template():
    tpl = templates.env.from_string("{{ i | shortid }} · {{ k | kindword }}")
    html = tpl.render(i="wo_3d7cd6464e2e4980a69acd0a3e031264", k="joint_stall")
    assert html == "wo_3d7cd6… · Joint stall"


def test_overview_counts_the_rows_waiting_on_a_person():
    rows = [
        {"tone": "you", "agent": None, "help": 0},
        {"tone": "you", "agent": None, "help": 0},
        {"tone": "bad", "agent": None, "help": 0},
        {"tone": "good", "agent": None, "help": 0},
    ]
    assert _overview(rows, {"devices": []})["you"] == 2


def _macros(src: str, **ctx) -> str:
    head = '{% from "partials/ui.html" import icon, heal_line %}'
    return templates.env.from_string(head + src).render(**ctx)


def test_icon_macro():
    assert _macros('{{ icon("hand") }}') == (
        '<svg class="icon" aria-hidden="true"><use href="#i-hand"/></svg>'
    )


STATIONS = [
    ("Healer gave up", "done"),
    ("Naïve took over", "done"),
    ("Technician emailed", "done"),
    ("Waiting on you", "you"),
]


def test_heal_line_full():
    html = _macros("{{ heal_line(stations) }}", stations=STATIONS)
    assert html.count('<li class="st done">') == 3
    assert '<li class="st you" aria-current="step">' in html
    assert html.count('<use href="#i-check"/>') == 3
    assert '<span class="lab">Waiting on you</span>' in html
    assert html.startswith('<ol class="line" aria-label="Heal path">')


def test_heal_line_mini_emphasises_the_last_label():
    html = _macros("{{ heal_line(stations, mini=true) }}", stations=STATIONS)
    assert '<i class="d"></i><i class="d"></i><i class="d"></i><i class="y"></i>' in html
    assert (
        '<span class="mini-lab">Healer gave up · Naïve took over · Technician emailed · '
        '<b class="y">Waiting on you</b></span>'
    ) in html


def test_heal_line_takes_the_technician_stage_dicts_and_its_words():
    stages = [
        {"label": "healer gave up", "state": "done"},
        {"label": "diagnosing", "state": "active"},
        {"label": "email not sent", "state": "skipped"},
        {"label": "denied", "state": "denied"},
        {"label": "approved by kent", "state": "acted"},
        {"label": "who knows", "state": "sideways"},
    ]
    html = _macros("{{ heal_line(stages) }}", stages=stages)
    for cls in ("done", "work", "todo", "bad", "you done-human"):
        assert f'<li class="st {cls}">' in html
    assert html.count('<li class="st todo">') == 2  # unknown states read as todo
    mini = _macros("{{ heal_line(stages, mini=true) }}", stages=stages)
    assert '<i class="d"></i><i class="w"></i><i class="t"></i><i class="b"></i>' in mini


def _page(**ctx) -> str:
    return templates.env.get_template("base.html").render(
        fleet={"frozen": False}, tz="America/Los_Angeles", **ctx
    )


def test_the_shell():
    html = _page()
    assert '<html lang="en" data-theme="day">' in html
    assert 'localStorage.setItem("romulus.theme"' in html
    assert html.index('<symbol id="i-mark"') < html.index('<header class="top">')
    assert html.count('class="sprite"') == 1
    assert '<a class="nav" href="/">Fleet</a>' in html
    assert '<a class="nav" href="/technician">Technician</a>' in html
    assert '<a class="nav" href="/data">Data</a>' in html
    nav = html[html.index('<nav class="nav-links"') : html.index("</nav>")]
    assert "aria-current" not in nav  # a script marks the current page, not the markup
    assert '<span class="live" id="live" title="Updates every 3 s"><i></i>Live</span>' in html
    assert "Times in America/Los_Angeles. Every step says who acted and why." in html
    assert "updates every 3 s" not in html
    assert "[data-live]" in html and "is-changed" in html
    assert "prefers-color-scheme" not in html


def test_the_kill_switch_at_rest_and_frozen():
    partial = templates.env.get_template("partials/killswitch.html")
    rest = partial.render(fleet={"frozen": False})
    assert (
        'class="switch freeze" data-switch="freeze" data-url="/v1/fleet/freeze">Freeze fleet<'
        in rest
    )
    frozen = partial.render(fleet={"frozen": True, "frozen_why": "human:kent froze the fleet"})
    assert 'class="killswitch frozen"' in frozen and "FLEET FROZEN" in frozen
    assert 'data-switch="unfreeze" data-url="/v1/fleet/unfreeze">Unfreeze<' in frozen
    assert "human:kent froze the fleet" in frozen
