import asyncio
import json
import uuid

import pytest

from app.builder import orchestrator as orch_mod
from app.builder.orchestrator import BuilderOrchestrator
from app.builder.spec import ProjectSpec
from app.builder.store import BuilderError, BuilderStore
from app.builder.validator import validate_project
from app.core.config import get_settings
from app.db.session import async_session_maker
from app.services.ai_provider import AICallOutcome, AIProvider, DeterministicAIProvider

pytestmark = pytest.mark.asyncio

TENANT, OTHER = uuid.uuid4(), uuid.uuid4()
USER = uuid.uuid4()
SAAS = "Build a premium AI SaaS marketing website with a homepage, features page, pricing page, blog listing, blog detail routes, about page, and contact page."
ESTATE = "Build a real-estate marketplace with a homepage, property search, filters, property details, saved properties, user accounts, and enquiry forms."


class FakeAI(AIProvider):
    is_connected = True
    name = "fake"
    model = "fake-model"

    def __init__(self, reply) -> None:
        self.reply, self.calls = reply, 0

    async def enrich_brief(self, *a, **k):
        return None, None

    async def generate_structured(self, prompt: str) -> AICallOutcome:
        self.calls += 1
        if isinstance(self.reply, Exception):
            return AICallOutcome(success=False, provider="fake", model="fake-model", latency_ms=1)
        text = self.reply if isinstance(self.reply, str) else json.dumps(self.reply)
        return AICallOutcome(success=True, provider="fake", model="fake-model", latency_ms=1, raw_text=text, input_tokens=100, output_tokens=200)


def make(provider=None) -> BuilderOrchestrator:
    return BuilderOrchestrator(async_session_maker, provider_factory=lambda: provider or DeterministicAIProvider())


async def build(o: BuilderOrchestrator, prompt: str = SAAS, name: str = "Acme"):
    p = await o.store.create_project(TENANT, name=name, prompt=prompt, created_by=USER)
    run = await o.start_create(TENANT, USER, p.id, prompt, name)
    await o.wait(run.id)
    return p, await o.store.get_run(TENANT, run.id)


# ---------------------------------------------------------------- scenario A: a multi-page project, end to end
async def test_create_builds_a_validated_multi_page_project_and_records_every_state():
    o = make()
    p, run = await build(o)
    assert run.state == "ready" and run.source == "deterministic" and run.result_version_id
    states = [e["state"] for e in run.progress]
    assert states[0] == "planning" and "generating" in states and "validating" in states and states[-1] == "ready"
    proj = await o.store.get_project(TENANT, p.id)
    spec = ProjectSpec.model_validate(proj.spec)
    assert {"/pricing", "/features", "/blog", "/blog/[slug]", "/about", "/contact"} <= {r.path for r in spec.routes}
    ws = await o.store.load_workspace(TENANT, p.id, proj.current_version_id)
    assert validate_project(ws, spec).ok and len(ws.paths()) > 15
    assert proj.status == "ready"
    msgs = await o.store.list_messages(TENANT, p.id)
    assert [m.role for m in msgs] == ["user", "assistant"] and "routes" in msgs[1].content


async def test_a_second_project_gets_a_different_sitemap():
    o = make()
    _, r1 = await build(o, SAAS, "A")
    p2 = await o.store.create_project(TENANT, name="B", prompt=ESTATE, created_by=USER)
    run2 = await o.start_create(TENANT, USER, p2.id, ESTATE, "B")
    await o.wait(run2.id)
    spec2 = ProjectSpec.model_validate((await o.store.get_project(TENANT, p2.id)).spec)
    assert "/properties/[slug]" in {r.path for r in spec2.routes} and "/pricing" not in {r.path for r in spec2.routes}


# ---------------------------------------------------------------- scenario C: iterate without breaking what exists
async def test_edit_creates_a_new_version_keeps_routes_and_leaves_unrelated_files_identical():
    o = make()
    p, _ = await build(o)
    proj = await o.store.get_project(TENANT, p.id)
    v1 = proj.current_version_id
    run = await o.start_edit(TENANT, USER, p.id, "make it more premium")
    await o.wait(run.id)
    run = await o.store.get_run(TENANT, run.id)
    assert run.state == "ready"
    proj2 = await o.store.get_project(TENANT, p.id)
    assert proj2.current_version_id != v1
    old = await o.store.load_workspace(TENANT, p.id, v1)
    new = await o.store.load_workspace(TENANT, p.id, proj2.current_version_id)
    assert set(old.paths()) == set(new.paths())
    changed = {m["path"] for m in new.manifest()} - {m["path"] for m in old.manifest() if any(x["path"] == m["path"] and x["sha"] == m["sha"] for x in new.manifest())}
    assert changed == {"assets/styles.css"}  # a design change touched the stylesheet, not one page
    versions = await o.store.list_versions(TENANT, p.id)
    assert [v.number for v in versions] == [2, 1] and versions[0].changes["changed"] == ["assets/styles.css"]


async def test_an_edit_that_changes_nothing_saves_no_version():
    o = make()
    p, _ = await build(o)
    run = await o.start_edit(TENANT, USER, p.id, "make the header sticky")
    await o.wait(run.id)
    assert (await o.store.get_run(TENANT, run.id)).state == "ready"
    assert len(await o.store.list_versions(TENANT, p.id)) == 1


async def test_add_page_edit_adds_the_route_and_keeps_the_others():
    o = make()
    p, _ = await build(o)
    run = await o.start_edit(TENANT, USER, p.id, "add a faq page")
    await o.wait(run.id)
    spec = ProjectSpec.model_validate((await o.store.get_project(TENANT, p.id)).spec)
    assert "/faq" in {r.path for r in spec.routes} and "/pricing" in {r.path for r in spec.routes}


# ---------------------------------------------------------------- scenario E: broken code, bounded repair
async def test_a_generator_defect_is_detected_repaired_and_only_then_reported_ready(monkeypatch):
    real = orch_mod.generate_project
    calls = {"n": 0}

    def flaky(spec, **kw):
        ws = real(spec, **kw)
        calls["n"] += 1
        if calls["n"] == 1:  # the first generation is defective: a page vanished and a script was injected
            ws.delete("pricing/index.html")
            ws.write("index.html", ws.read("index.html").replace("</body>", "<script>alert(1)</script></body>"))
        return ws

    monkeypatch.setattr(orch_mod, "generate_project", flaky)
    o = make()
    p, run = await build(o)
    assert run.state == "ready" and run.repair_attempts == 1
    assert [e["state"] for e in run.progress].count("repairing") == 1
    proj = await o.store.get_project(TENANT, p.id)
    ws = await o.store.load_workspace(TENANT, p.id, proj.current_version_id)
    assert "pricing/index.html" in ws.paths() and "alert(1)" not in ws.read("index.html")
    assert validate_project(ws, ProjectSpec.model_validate(proj.spec)).ok


async def test_when_repair_fails_nothing_is_saved_and_the_last_good_version_survives(monkeypatch):
    o = make()
    p, _ = await build(o)
    good = (await o.store.get_project(TENANT, p.id)).current_version_id
    real = orch_mod.generate_project

    def always_broken(spec, **kw):
        ws = real(spec, **kw)
        ws.write("index.html", ws.read("index.html").replace("</body>", "<script>alert(1)</script></body>"))
        return ws

    monkeypatch.setattr(orch_mod, "generate_project", always_broken)
    run = await o.start_edit(TENANT, USER, p.id, "switch to a dark theme")
    await o.wait(run.id)
    run = await o.store.get_run(TENANT, run.id)
    assert run.state == "failed" and run.repair_attempts == get_settings().BUILDER_MAX_REPAIR_ATTEMPTS
    assert "inline" in (run.error or "").lower() or "script" in (run.error or "").lower()
    proj = await o.store.get_project(TENANT, p.id)
    assert proj.current_version_id == good and proj.status == "ready"
    assert len(await o.store.list_versions(TENANT, p.id)) == 1


# ---------------------------------------------------------------- scenario G: failures, cancellation, recovery
async def test_cancelling_a_run_saves_nothing(monkeypatch):
    started = asyncio.Event()

    async def slow_plan(*a, **k):
        started.set()
        await asyncio.sleep(30)

    monkeypatch.setattr(orch_mod, "plan_project", slow_plan)
    o = make()
    p = await o.store.create_project(TENANT, name="Slow", prompt=SAAS, created_by=USER)
    run = await o.start_create(TENANT, USER, p.id, SAAS, "Slow")
    await started.wait()
    await o.cancel(TENANT, run.id)
    await o.wait(run.id)
    run = await o.store.get_run(TENANT, run.id)
    assert run.state == "cancelled" and run.cancel_requested
    proj = await o.store.get_project(TENANT, p.id)
    assert proj.current_version_id is None and len(await o.store.list_versions(TENANT, p.id)) == 0
    # and a new run can start straight away
    run2 = await o.start_create(TENANT, USER, p.id, SAAS, "Slow")
    monkeypatch.undo()
    await o.cancel(TENANT, run2.id)
    await o.wait(run2.id)


async def test_a_run_that_takes_too_long_is_stopped_and_reported(monkeypatch):
    async def never(*a, **k):
        await asyncio.sleep(30)

    monkeypatch.setattr(orch_mod, "plan_project", never)
    monkeypatch.setattr(get_settings(), "BUILDER_RUN_TIMEOUT_SECONDS", 0.2)
    o = make()
    p = await o.store.create_project(TENANT, name="T", prompt=SAAS, created_by=USER)
    run = await o.start_create(TENANT, USER, p.id, SAAS, "T")
    await o.wait(run.id)
    run = await o.store.get_run(TENANT, run.id)
    assert run.state == "failed" and "too long" in " ".join(e["message"] for e in run.progress)


@pytest.mark.parametrize("reply", ["this is not json at all", {"unexpected": True}, {"routes": [{"path": "/x", "title": "no home"}]}, Exception("provider down"), '{"name": "x", "routes": "nope"}'])
async def test_bad_model_output_or_provider_failure_falls_back_cleanly(reply):
    o = make(FakeAI(reply))
    p, run = await build(o)
    assert run.state == "ready" and run.source == "deterministic"
    proj = await o.store.get_project(TENANT, p.id)
    assert validate_project(await o.store.load_workspace(TENANT, p.id, proj.current_version_id), ProjectSpec.model_validate(proj.spec)).ok


async def test_a_good_ai_plan_is_used_validated_and_costed():
    reply = {
        "name": "Fig & Co", "summary": "A neighbourhood bakery.", "design": {"direction": "minimal_editorial"},
        "routes": [
            {"path": "/", "title": "Home", "sections": [{"kind": "hero", "heading": "Bread, baked daily", "body": "Small batches from a corner bakery.", "cta_label": "See the menu", "cta_href": "/menu"}]},
            {"path": "/menu", "title": "Menu", "entity": "items", "sections": [{"kind": "menu", "entity": "items", "heading": "What we bake"}]},
            {"path": "/menu/[slug]", "title": "Item", "nav": False, "entity": "items", "sections": [{"kind": "detail", "entity": "items"}]},
        ],
        "entities": [{"name": "items", "singular": "Loaf", "fields": [{"name": "price", "type": "price", "sortable": True}], "records": [{"slug": "sourdough", "title": "Sourdough", "price": 6}, {"slug": "rye", "title": "Rye", "price": 7}]}],
    }
    fake = FakeAI(reply)
    o = make(fake)
    p, run = await build(o, "A bakery site")
    assert run.state == "ready" and run.source == "ai"
    assert run.usage["calls"] == 1 and run.usage["input_tokens"] == 100 and run.usage["output_tokens"] == 200 and "fake-model" in run.usage["models"]
    spec = ProjectSpec.model_validate((await o.store.get_project(TENANT, p.id)).spec)
    assert spec.name == "Fig & Co" and [r.path for r in spec.routes] == ["/", "/menu", "/menu/[slug]"]


async def test_stale_runs_are_marked_failed_on_recovery():
    o = make()
    p = await o.store.create_project(TENANT, name="R", prompt="x", created_by=USER)
    run = await o.store.create_run(TENANT, p.id, kind="create", prompt="x", created_by=USER)
    await o.store.set_state(TENANT, run.id, "generating", "working")
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import update
    from app.models.builder import BuilderRun

    async with async_session_maker() as s:
        await s.execute(update(BuilderRun).where(BuilderRun.id == run.id).values(heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=2)))
        await s.commit()
    assert await o.store.recover_stale_runs() == 1
    r = await o.store.get_run(TENANT, run.id)
    assert r.state == "failed" and "interrupted" in r.error


# ---------------------------------------------------------------- scenario F: isolation
async def test_another_tenant_cannot_see_touch_or_cancel_anything():
    o = make()
    p, run = await build(o)
    proj = await o.store.get_project(TENANT, p.id)
    for call in (
        lambda: o.store.get_project(OTHER, p.id),
        lambda: o.store.get_run(OTHER, run.id),
        lambda: o.store.get_version(OTHER, p.id, proj.current_version_id),
        lambda: o.cancel(OTHER, run.id),
        lambda: o.start_edit(OTHER, USER, p.id, "add a faq page"),
        lambda: o.publish(OTHER, p.id, acknowledge_examples=True),
        lambda: o.save_file(OTHER, USER, p.id, "index.html", "<p>x</p>"),
        lambda: o.rollback(OTHER, USER, p.id, proj.current_version_id),
        lambda: o.store.delete_project(OTHER, p.id),
    ):
        with pytest.raises(BuilderError) as e:
            await call()
        assert e.value.status in (404, 409)
    assert (await o.store.load_workspace(OTHER, p.id, proj.current_version_id)).paths() == []
    assert await o.store.list_projects(OTHER) == []
    assert (await o.store.get_project(TENANT, p.id)).current_version_id == proj.current_version_id


# ---------------------------------------------------------------- limits
async def test_only_one_build_may_run_per_tenant_at_a_time(monkeypatch):
    gate = asyncio.Event()

    async def slow(*a, **k):
        await gate.wait()

    monkeypatch.setattr(orch_mod, "plan_project", slow)
    o = make()
    p = await o.store.create_project(TENANT, name="One", prompt=SAAS, created_by=USER)
    run = await o.start_create(TENANT, USER, p.id, SAAS, "One")
    p2 = await o.store.create_project(TENANT, name="Two", prompt=SAAS, created_by=USER)
    with pytest.raises(BuilderError) as e:
        await o.start_create(TENANT, USER, p2.id, SAAS, "Two")
    assert e.value.status == 409
    other = await o.store.create_project(OTHER, name="Other tenant", prompt=SAAS, created_by=USER)
    monkeypatch.undo()
    r2 = await o.start_create(OTHER, USER, other.id, SAAS, "x")  # another tenant is not blocked
    await o.wait(r2.id)
    await o.cancel(TENANT, run.id)
    await o.wait(run.id)


async def test_hourly_run_limit(monkeypatch):
    monkeypatch.setattr(get_settings(), "BUILDER_MAX_RUNS_PER_TENANT_PER_HOUR", 2)
    o = make()
    p, _ = await build(o)
    for _ in range(1):
        run = await o.start_edit(TENANT, USER, p.id, "add a faq page")
        await o.wait(run.id)
    with pytest.raises(BuilderError) as e:
        await o.start_edit(TENANT, USER, p.id, "add a docs page")
    assert e.value.status == 429


async def test_project_quota(monkeypatch):
    monkeypatch.setattr(get_settings(), "BUILDER_MAX_PROJECTS_PER_TENANT", 2)
    o = make()
    await o.store.create_project(TENANT, name="a", prompt="", created_by=USER)
    await o.store.create_project(TENANT, name="a", prompt="", created_by=USER)  # same name gets a distinct slug
    with pytest.raises(BuilderError):
        await o.store.create_project(TENANT, name="c", prompt="", created_by=USER)


# ---------------------------------------------------------------- versions, files, publishing
async def test_rollback_restores_an_earlier_version_as_a_new_one():
    o = make()
    p, _ = await build(o)
    v1 = (await o.store.get_project(TENANT, p.id)).current_version_id
    run = await o.start_edit(TENANT, USER, p.id, "switch to a dark theme")
    await o.wait(run.id)
    v3 = await o.rollback(TENANT, USER, p.id, v1)
    assert v3.number == 3 and v3.kind == "rollback"
    a, b = await o.store.load_workspace(TENANT, p.id, v1), await o.store.load_workspace(TENANT, p.id, v3.id)
    assert a.manifest() == b.manifest()


async def test_hand_edits_must_pass_checks_and_survive_regeneration():
    o = make()
    p, _ = await build(o)
    proj = await o.store.get_project(TENANT, p.id)
    ws = await o.store.load_workspace(TENANT, p.id, proj.current_version_id)
    good = ws.read("about/index.html").replace("</main>", "<p>Our story, in our own words.</p></main>")
    v = await o.save_file(TENANT, USER, p.id, "about/index.html", good)
    assert v.kind == "file_edit"
    with pytest.raises(BuilderError) as e:
        await o.save_file(TENANT, USER, p.id, "index.html", "<html><body><script>alert(1)</script></body></html>")
    assert e.value.status == 422
    with pytest.raises(BuilderError):
        await o.save_file(TENANT, USER, p.id, "../../etc/passwd.html", "x")
    run = await o.start_edit(TENANT, USER, p.id, "switch to a dark theme")
    await o.wait(run.id)
    cur = await o.store.get_project(TENANT, p.id)
    after = await o.store.load_workspace(TENANT, p.id, cur.current_version_id)
    assert "Our story, in our own words." in after.read("about/index.html")


async def test_publishing_needs_acknowledgement_of_example_content_and_serves_only_the_published_version():
    o = make()
    p, _ = await build(o)
    with pytest.raises(BuilderError) as e:
        await o.publish(TENANT, p.id, acknowledge_examples=False)
    assert e.value.status == 409 and "example content" in str(e.value)
    assert await o.store.get_published(TENANT, (await o.store.get_project(TENANT, p.id)).slug) is None
    v = await o.publish(TENANT, p.id, acknowledge_examples=True)
    slug = (await o.store.get_project(TENANT, p.id)).slug
    pub = await o.store.get_published(TENANT, slug)
    assert pub.published_version_id == v.id
    run = await o.start_edit(TENANT, USER, p.id, "switch to a dark theme")  # a later edit does not change what is live
    await o.wait(run.id)
    assert (await o.store.get_published(TENANT, slug)).published_version_id == v.id


async def test_old_versions_are_pruned_but_current_and_published_are_kept(monkeypatch):
    monkeypatch.setattr(get_settings(), "BUILDER_VERSIONS_KEPT", 2)
    o = make()
    p, _ = await build(o)
    v1 = await o.publish(TENANT, p.id, acknowledge_examples=True)
    for msg in ("switch to a dark theme", "use a serif typeface", "give it more spacing", "use a centered hero"):
        run = await o.start_edit(TENANT, USER, p.id, msg)
        await o.wait(run.id)
        monkeypatch.setattr(get_settings(), "BUILDER_MAX_RUNS_PER_TENANT_PER_HOUR", 99)
    nums = [v.number for v in await o.store.list_versions(TENANT, p.id)]
    assert 1 in nums and nums[0] == 5 and len(nums) == 3  # newest two + the published one


async def test_export_is_a_zip_of_the_version_with_safe_names():
    import io, zipfile

    o = make()
    p, _ = await build(o)
    proj = await o.store.get_project(TENANT, p.id)
    data = await o.export_zip(TENANT, p.id, proj.current_version_id)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "index.html" in names and "README.md" in names and all(not n.startswith("/") and ".." not in n for n in names)


async def test_deleting_a_project_removes_its_versions_files_runs_and_messages():
    o = make()
    p, _ = await build(o)
    await o.store.delete_project(TENANT, p.id)
    from sqlalchemy import func, select
    from app.models.builder import BuilderFile, BuilderMessage, BuilderRun, BuilderVersion

    async with async_session_maker() as s:
        for m in (BuilderFile, BuilderVersion, BuilderRun, BuilderMessage):
            assert (await s.execute(select(func.count()).select_from(m))).scalar_one() == 0
