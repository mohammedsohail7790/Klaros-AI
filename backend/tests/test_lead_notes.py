"""Notes on an enquiry: a person or an AI crew member can record an assessment before the enquiry is a customer. Found by running the live flow, where the crew's note tool needed a
customer and a brand-new enquiry has none. Consent still applies, and a model that calls the id by another name is understood."""

import uuid

import pytest

from app.api.tool_deps import get_tool_registry
from app.models.actor import ActorType
from app.models.rbac import Role
from app.services import consent_gate
from app.tools.base import ExecutionContext
from app.tools.policy import DEFAULT_TOOL_POLICIES, ActionPolicy
from tests.test_consent_gate_intake import ALL, _body
from tests.test_mt_referrals import _gated, _h, _patient, halla  # noqa: F401
from tests.test_halla_integration import _lead, _register, _tenant_id

pytestmark = pytest.mark.asyncio


def _ctx(tid, actor=ActorType.AI):
    return ExecutionContext(tenant_id=tid, actor_type=actor, actor_id=uuid.uuid4(), role=Role.MANAGER)


async def test_the_tools_are_registered_and_need_no_approval() -> None:
    for tool in ("crm.add_lead_note", "crm.list_lead_notes"):
        assert DEFAULT_TOOL_POLICIES[tool] == ActionPolicy.AUTO
        get_tool_registry().get(tool)


async def test_a_note_is_added_to_an_enquiry_with_no_customer_and_listed_in_the_api(client) -> None:
    token = await _register(client, "Note Co", "noteco@example.com")
    tid = await _tenant_id(client, token)
    lead = await _lead(client, token, phone="+971500000040")
    reg = get_tool_registry()
    added = await reg.execute("crm.add_lead_note", {"lead_id": lead, "body": "Qualified: wants a quote. Next step: send availability."}, _ctx(tid))
    assert added.note_id
    listed = await reg.execute("crm.list_lead_notes", {"lead_id": lead}, _ctx(tid))
    assert [n["body"] for n in listed.notes] == ["Qualified: wants a quote. Next step: send availability."] and listed.notes[0]["author_type"] == "AI"
    api = (await client.get(f"/api/v1/leads/{lead}/notes", headers=_h(token))).json()
    assert [n["body"] for n in api["notes"]] == [listed.notes[0]["body"]]


async def test_a_model_that_calls_the_lead_id_the_enquiry_id_is_understood(client) -> None:
    token = await _register(client, "Alias Co", "aliasco@example.com")
    tid = await _tenant_id(client, token)
    lead = await _lead(client, token, phone="+971500000041")
    assert (await get_tool_registry().execute("crm.add_lead_note", {"enquiry_id": lead, "body": "ok"}, _ctx(tid))).note_id


async def test_one_business_cannot_read_or_write_another_businesss_notes(client) -> None:
    a = await _register(client, "Notes A Co", "notesa@example.com")
    b = await _register(client, "Notes B Co", "notesb@example.com")
    a_tid, b_tid = await _tenant_id(client, a), await _tenant_id(client, b)
    lead = await _lead(client, a, phone="+971500000042")
    with pytest.raises(ValueError):
        await get_tool_registry().execute("crm.add_lead_note", {"lead_id": lead, "body": "not yours"}, _ctx(b_tid))
    await get_tool_registry().execute("crm.add_lead_note", {"lead_id": lead, "body": "mine"}, _ctx(a_tid))
    assert (await get_tool_registry().execute("crm.list_lead_notes", {"lead_id": lead}, _ctx(b_tid))).notes == []


async def test_a_business_that_handles_patient_data_stores_a_note_only_with_consent_for_health_information(client, halla) -> None:  # noqa: F811
    token, tid = await _gated(client, "Gated Notes Co", "gatednotes@example.com")
    full = await _patient(client, token)  # consents to everything
    assert (await get_tool_registry().execute("crm.add_lead_note", {"lead_id": full, "body": "assessment"}, _ctx(tid))).note_id
    narrow = await _patient(client, token, scopes=("contact", "store_personal_data"))  # no consent to store health information
    with pytest.raises(consent_gate.ConsentRequiredError):
        await get_tool_registry().execute("crm.add_lead_note", {"lead_id": narrow, "body": "mentions a condition"}, _ctx(tid))
    assert (await get_tool_registry().execute("crm.list_lead_notes", {"lead_id": narrow}, _ctx(tid))).notes == []


async def test_the_crew_members_that_record_assessments_use_the_lead_note_tool() -> None:
    from app.services import operating_crew as oc

    for crew in oc.PLAYBOOKS.values():
        for m in crew:
            assert "crm.create_note" not in m.tools, f"{m.key} writes to a customer note that a new enquiry does not have"
    keys_with_notes = {m.key for crew in oc.PLAYBOOKS.values() for m in crew if "crm.add_lead_note" in m.tools}
    assert {"lead-responder", "booking-coordinator", "clinic-matcher", "safety-triage"} <= keys_with_notes
