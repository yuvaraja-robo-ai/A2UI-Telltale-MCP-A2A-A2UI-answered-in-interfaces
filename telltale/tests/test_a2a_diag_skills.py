"""A peer agent asking the diagnostics agent to run, over A2A.

The UI path is not the only way in. Another agent can send a message naming a
skill this agent advertises on its card, and get back the same evidence the
interface shows: a readable summary, the structured result, and the composed
A2UI surface so a peer that can render one does not have to rebuild it.

A skill tag is the first token of the message text. Anything not carrying a
recognised tag is none of this module's business and falls through to the
runtime's normal grounded-answer graph, so adding diagnostics did not narrow
what the agent already accepted.
"""
from __future__ import annotations

import pytest

from telltale.vehicle import a2a_diag


def parts_of(result):
    return {part["kind"]: part for part in result}


# --------------------------------------------------------------------------- #
# what the agent advertises
# --------------------------------------------------------------------------- #

def test_the_card_advertises_both_skills_by_their_tag() -> None:
    """A peer discovers the tag from the agent card; it should not have to be
    told out of band."""
    ids = {skill["id"] for skill in a2a_diag.SKILLS}

    assert ids == {"telltale.status", "telltale.diagnose"}
    for skill in a2a_diag.SKILLS:
        assert skill["description"]
        assert skill["tags"]


# --------------------------------------------------------------------------- #
# routing on the tag
# --------------------------------------------------------------------------- #

def test_a_message_with_no_recognised_tag_is_not_claimed() -> None:
    """Returning None is how this module says "not mine" — the caller then runs
    whatever it ran before."""
    assert a2a_diag.route("what is the capital of France?") is None
    assert a2a_diag.route("") is None


def test_a_tag_that_looks_like_ours_but_is_not_a_skill_is_an_error() -> None:
    """Claimed-but-unknown is different from unclaimed: silently falling back
    would let a typo look like it worked."""
    with pytest.raises(ValueError, match="unknown Telltale skill"):
        a2a_diag.route("telltale.wipe_dtc")


@pytest.mark.parametrize("tag", ["telltale.status", "telltale.diagnose"])
def test_a_recognised_tag_is_claimed_and_executed(tag: str) -> None:
    result = a2a_diag.route(tag)

    assert result is not None
    assert parts_of(result).keys() >= {"text", "data"}


def test_the_tag_is_matched_before_any_trailing_words(tag: str = "telltale.diagnose") -> None:
    result = a2a_diag.route(f"{tag} please check the front axle")

    assert result is not None


# --------------------------------------------------------------------------- #
# what comes back
# --------------------------------------------------------------------------- #

def test_a_diagnose_reply_leads_with_a_readable_verdict() -> None:
    text = parts_of(a2a_diag.route("telltale.diagnose"))["text"]["text"]

    assert "checks failed" in text
    assert "trouble code" in text


def test_a_diagnose_reply_names_each_failure_with_its_reading_and_limit() -> None:
    """A peer agent acting on this needs the number, not the adjective."""
    text = parts_of(a2a_diag.route("telltale.diagnose"))["text"]["text"]

    assert "BrakeFluidLevel" in text
    assert "41" in text and "45" in text


def test_a_diagnose_reply_carries_the_structured_result(tag: str = "telltale.diagnose") -> None:
    data = parts_of(a2a_diag.route(tag))["data"]["data"]

    assert data["skill"] == tag
    assert data["checksRun"] >= 15
    assert data["checksFailed"] > 0
    assert len(data["dtcs"]) == 7
    assert data["source"] in ("bench", "live")


def test_a_diagnose_reply_carries_a_surface_a_peer_can_render() -> None:
    """The peer gets the interface too. Rebuilding it from the raw numbers is
    how two agents end up disagreeing about the same vehicle."""
    data = parts_of(a2a_diag.route("telltale.diagnose"))["data"]["data"]

    assert data["surface"]["components"]
    assert data["surface"]["root"]


def test_a_returned_surface_has_already_passed_the_validator() -> None:
    data = parts_of(a2a_diag.route("telltale.diagnose"))["data"]["data"]

    assert data["surfaceClean"] is True


def test_a_status_reply_summarises_the_vehicle_rather_than_the_checks() -> None:
    data = parts_of(a2a_diag.route("telltale.status"))["data"]["data"]

    assert data["skill"] == "telltale.status"
    assert data["health"] == "critical"
    assert data["signalCount"] == 40
    assert data["domainCount"] == 8
