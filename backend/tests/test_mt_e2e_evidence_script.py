"""The evidence collector prints ids/counts/scopes only -- never a name, phone, email or free text -- and reports duplicates, state and isolation."""
import json

from tests.test_consent_gate_intake import ALL, PII_EMAIL, PII_NAME, PII_PHONE, _gated, _body
from tests.test_halla_contract_mt import HTENANT, SECRET, _life_raw, _mt  # noqa: F401
from tests.test_halla_integration import _deliver, halla  # noqa: F401


async def test_evidence_output_has_no_personal_data_and_shows_state_duplicates_and_isolation(client, halla) -> None:  # noqa: F811
    from scripts.mt_e2e_evidence import gather

    token, tid = await _mt(client, "MT Evidence", "mtevidence@example.com")
    _t2, other = await _mt(client, "MT Evidence Other", "mtevidence2@example.com")
    for i in (0, 1):
        await _deliver(client, tid, _life_raw(i), secret=SECRET)
    await _deliver(client, tid, _life_raw(1), secret=SECRET)  # duplicate delivery
    out = await gather(tid, halla_lead_id="halla-lead-9001", other_tenant=other)
    text = json.dumps(out, default=str)
    for secret in ("Synthetic", "+1555", "@example.com", SECRET):
        assert secret not in text
    assert out["leads_total"] == 1 and len(out["leads_linked_to_halla_lead"]) == 1 and out["duplicate_leads_for_halla_lead"] == 0
    assert out["derived_consent_now"] == {"evidence_recorded": True, "granted_scopes": ["contact", "store_personal_data"]}
    assert len(out["consent_evidence"]) == 2 and out["duplicate_evidence_event_ids"] == 0
    assert out["isolation"]["leads_with_same_halla_lead_id"] == 0 and out["isolation"]["evidence_with_same_halla_lead_id"] == 0
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    head = ScriptDirectory.from_config(Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))).get_current_head()
    assert out["alembic_revision"] in (None, head)  # the CURRENT migration head (never a stale pinned revision); None when the test database was built without migrations
