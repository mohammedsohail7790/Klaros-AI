"""Approved wording blocks for clinic outreach: validation rules, defaults, and the service's save / approve / gate behaviour. SQLite; nothing is sent."""
from __future__ import annotations

import copy
import re
import uuid

import pytest

from app.db.session import async_session_maker
from app.services.clinic_outreach_service import ClinicOutreachService, OutreachError
from app.services.outreach_blocks import BANNED_PHRASES, DEFAULT_BLOCKS, validate_blocks
from tests.test_clinic_outreach_service import USER, _audits, _clinic, _config, _svc

KEYS = {"subject_intro", "intro", "subject_followup_1", "followup_1", "subject_followup_2", "followup_2", "opt_out_line",
        "how_referrals_work", "faq_what_we_do", "faq_what_we_need", "faq_commission"}


def _blocks(**over) -> dict[str, str]:
    return {**copy.deepcopy(DEFAULT_BLOCKS), **over}


def test_default_blocks_are_valid() -> None:
    assert set(DEFAULT_BLOCKS) == KEYS
    assert validate_blocks(DEFAULT_BLOCKS) == {}
    assert not any(re.search(r"\d", text) for text in DEFAULT_BLOCKS.values())


def test_a_digit_in_any_block_is_rejected() -> None:
    for key in KEYS:
        problems = validate_blocks(_blocks(**{key: DEFAULT_BLOCKS[key] + " 5"}))
        assert any("digit" in p for p in problems.get(key, [])), key


@pytest.mark.parametrize("text", ["We pay 10% of fees.", "Fees are ten percent.", "Fees are ten per cent.", "Pay within five days.", "Pay within thirty days of treatment.",
                                  "Reply within two weeks.", "Reply within ninety days.", "Within one month we reply.", "Pay within a hundred days.", "Pay within hundred days.",
                                  "A fee of USD 200 applies.", "Reply within a week.", "Reply within a fortnight.", "Reply within a month.", "Reply within a dozen days.",
                                  "Reply within two fortnights."])
def test_a_percentage_or_amount_or_day_count_is_rejected(text) -> None:
    assert "faq_commission" in validate_blocks(_blocks(faq_commission=text))


def test_unknown_placeholder_is_rejected() -> None:
    problems = validate_blocks(_blocks(faq_what_we_do="Hello {patient_name}."))
    assert any("patient_name" in p for p in problems["faq_what_we_do"])


@pytest.mark.parametrize("phrase", BANNED_PHRASES)
def test_each_banned_phrase_is_rejected(phrase) -> None:
    problems = validate_blocks(_blocks(faq_what_we_do=f"We are an {phrase.upper()} here."))
    assert any(phrase in p.lower() for p in problems["faq_what_we_do"])


@pytest.mark.parametrize("kind", ["intro", "followup_1", "followup_2"])
@pytest.mark.parametrize("token", ["{business_name}", "{postal_address}", "{opt_out_line}"])
def test_composed_kinds_require_business_name_address_and_opt_out(kind, token) -> None:
    problems = validate_blocks(_blocks(**{kind: DEFAULT_BLOCKS[kind].replace(token, "")}))
    assert any(token in p for p in problems[kind])


def test_blank_opt_out_line_and_missing_or_non_string_blocks_are_rejected() -> None:
    assert "opt_out_line" in validate_blocks(_blocks(opt_out_line="   "))
    missing = {k: v for k, v in DEFAULT_BLOCKS.items() if k != "intro"}
    assert "intro" in validate_blocks(missing)
    assert "intro" in validate_blocks(_blocks(intro=7))  # type: ignore[arg-type]


def test_length_limits() -> None:
    assert "subject_intro" in validate_blocks(_blocks(subject_intro="Hello {business_name} " + "x" * 120))
    assert "subject_intro" not in validate_blocks(_blocks(subject_intro=("x" * 120)))
    assert "faq_what_we_do" in validate_blocks(_blocks(faq_what_we_do="x" * 1501))
    assert "faq_what_we_do" not in validate_blocks(_blocks(faq_what_we_do="x" * 1500))


async def _campaign_tenant():
    tid = uuid.uuid4()
    await _clinic(tid, "Alpha Clinic")
    svc = _svc()
    cid = await svc.create_campaign(tid, USER, name="C", config=_config(), provider_ids=None)
    return svc, tid, cid


async def test_get_returns_the_default_draft_for_a_new_tenant() -> None:
    got = await _svc().get_blocks(uuid.uuid4())
    assert got["status"] == "DRAFT" and got["version"] == 0 and got["blocks"] == DEFAULT_BLOCKS and got["approved_by"] is None and got["approved_at"] is None


async def test_save_resets_to_draft_and_bumps_version() -> None:
    svc, tid = _svc(), uuid.uuid4()
    first = await svc.save_blocks(tid, USER, _blocks(faq_what_we_do="We help people compare clinics abroad."))
    assert first["version"] == 1 and first["status"] == "DRAFT" and first["blocks"]["faq_what_we_do"] == "We help people compare clinics abroad."
    second = await svc.save_blocks(tid, USER, _blocks())
    assert second["version"] == 2
    assert (await svc.get_blocks(tid))["version"] == 2


async def test_save_rejects_invalid_unknown_and_non_string_blocks_with_the_problems() -> None:
    svc, tid = _svc(), uuid.uuid4()
    with pytest.raises(OutreachError) as exc:
        await svc.save_blocks(tid, USER, _blocks(faq_commission="Pay ten percent."))
    assert isinstance(exc.value.args[0], str) and "faq_commission" in exc.value.args[1]
    with pytest.raises(OutreachError) as exc:
        await svc.save_blocks(tid, USER, {**_blocks(), "extra": "x"})
    assert "extra" in exc.value.args[1]
    with pytest.raises(OutreachError) as exc:
        await svc.save_blocks(tid, USER, _blocks(intro=3))  # type: ignore[arg-type]
    assert "intro" in exc.value.args[1]
    assert (await svc.get_blocks(tid))["version"] == 0  # nothing stored


async def test_approve_records_who_and_when_and_audits_ids_only() -> None:
    svc, tid = _svc(), uuid.uuid4()
    await svc.save_blocks(tid, USER, _blocks(faq_what_we_do="Distinctive wording marker."))
    done = await svc.approve_blocks(tid, USER)
    assert done["status"] == "APPROVED" and done["approved_by"] == str(USER) and done["approved_at"] and done["version"] == 1
    assert await svc.blocks_approved(tid) is True
    (audit,) = await _audits(tid, "outreach.blocks_approved")
    assert audit.actor_id == USER and audit.input_summary == {"tenant_id": str(tid), "approved_by": str(USER), "version": 1}
    assert "Distinctive" not in str(audit.input_summary)
    with pytest.raises(OutreachError):
        await svc.approve_blocks(tid, USER)  # already approved


async def test_approve_of_the_defaults_works_and_invalid_stored_blocks_cannot_be_approved() -> None:
    svc, tid = _svc(), uuid.uuid4()
    assert (await svc.approve_blocks(tid, USER))["status"] == "APPROVED"
    from app.services.tenant_settings_service import OUTREACH_BLOCKS, TenantSettingsService
    bad = uuid.uuid4()
    await TenantSettingsService(async_session_maker).set(bad, OUTREACH_BLOCKS, {"status": "DRAFT", "version": 1, "blocks": _blocks(intro="Hi 5")}, updated_by=None)
    with pytest.raises(OutreachError) as exc:
        await svc.approve_blocks(bad, USER)
    assert "intro" in exc.value.args[1]
    assert await svc.blocks_approved(bad) is False


async def test_editing_after_approval_reopens_draft() -> None:
    svc, tid = _svc(), uuid.uuid4()
    await svc.approve_blocks(tid, USER)
    saved = await svc.save_blocks(tid, USER, _blocks(faq_what_we_do="Changed wording."))
    assert saved["status"] == "DRAFT" and saved["approved_by"] is None and saved["approved_at"] is None and saved["version"] == 1
    assert await svc.blocks_approved(tid) is False


async def test_blocks_approved_never_raises(monkeypatch) -> None:
    svc = _svc()

    async def boom(*a, **k):
        raise RuntimeError("settings down")

    monkeypatch.setattr(svc, "get_blocks", boom)
    assert await svc.blocks_approved(uuid.uuid4()) is False


async def test_start_shadow_refuses_until_blocks_are_approved() -> None:
    svc, tid, cid = await _campaign_tenant()
    with pytest.raises(OutreachError, match="The clinic email wording has not been approved yet; approve it first."):
        await svc.start_shadow(tid, USER, cid)
    await svc.approve_blocks(tid, USER)
    await svc.start_shadow(tid, USER, cid)
    # editing the wording again closes the gate for a restart
    await svc.pause(tid, USER, cid)
    await svc.save_blocks(tid, USER, _blocks())
    with pytest.raises(OutreachError, match="not been approved"):
        await svc.start_shadow(tid, USER, cid)


async def test_blocks_are_tenant_isolated() -> None:
    svc, a, b = _svc(), uuid.uuid4(), uuid.uuid4()
    await svc.save_blocks(a, USER, _blocks(faq_what_we_do="Tenant A wording."))
    await svc.approve_blocks(a, USER)
    assert await svc.blocks_approved(b) is False
    got = await svc.get_blocks(b)
    assert got["version"] == 0 and got["blocks"] == DEFAULT_BLOCKS
    assert (await svc.get_blocks(a))["blocks"]["faq_what_we_do"] == "Tenant A wording."


@pytest.mark.parametrize("placeholder", ["clinic_name", "business_name", "sender_name", "sender_title", "postal_address", "opt_out_line", "commission", "payment_days"])
def test_opt_out_line_is_plain_text_and_rejects_every_placeholder(placeholder) -> None:
    problems = validate_blocks(_blocks(opt_out_line='Reply "stop" {' + placeholder + "}."))
    assert any("plain text" in p for p in problems["opt_out_line"])
    assert validate_blocks(DEFAULT_BLOCKS) == {}


@pytest.mark.parametrize("phrase", ["partners of ours", "we have partnered with others", "Partnered With a group"])
def test_partner_variants_are_rejected(phrase) -> None:
    assert "faq_what_we_do" in validate_blocks(_blocks(faq_what_we_do=phrase))
