"""AI content/SEO drafting. No `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` is
configured in this environment, so this is a deterministic, template-based
generator grounded in real job/customer data — never a fabricated LLM
response labeled as AI output. `is_llm_connected()` reports the honest
state; callers (tools/API) surface "AI CONTENT GENERATION NOT CONNECTED"
using it rather than silently pretending a model ran.
"""

from dataclasses import dataclass

from app.core.config import get_settings


def is_llm_connected() -> bool:
    s = get_settings()
    return bool(s.ANTHROPIC_API_KEY or s.OPENAI_API_KEY)


@dataclass
class JobContentInput:
    service_type: str | None
    city: str | None
    job_title: str
    notes: str | None
    photo_count: int


@dataclass
class GeneratedCaption:
    text: str
    source: str  # "deterministic_template" or "llm" (never claimed unless is_llm_connected())


def generate_job_caption(job: JobContentInput) -> GeneratedCaption:
    """A real, grounded caption from real job fields — no invented
    outcomes, customer quotes, or statistics. If `is_llm_connected()` is
    False (the only case in this environment), this deterministic template
    is the entire implementation, not a fallback for a "broken" LLM call."""
    service = job.service_type or "service"
    location = f" in {job.city}" if job.city else ""
    photo_note = f" ({job.photo_count} photo{'s' if job.photo_count != 1 else ''} from the job)" if job.photo_count else ""
    text = f"Another {service} job completed{location}: {job.job_title}.{photo_note}"
    if job.notes:
        text += f" Notes from the crew: {job.notes.strip()}"
    return GeneratedCaption(text=text, source="deterministic_template")


def generate_seo_page_draft(service: str, location: str) -> dict:
    """Deterministic SEO page draft — same honesty rule as the caption
    generator above. Marked `ai_generated=True` on the `SEOPage` row
    regardless, since it IS machine-generated (template-based counts), but
    never claims to be an LLM's output."""
    title = f"{service} in {location} | Professional {service} Services"
    return {
        "title": title,
        "meta_title": title[:60],
        "meta_description": f"Need {service} in {location}? Fast, reliable, licensed local service. Call today for a free estimate.",
        "h1": f"{service} in {location}",
        "body_draft": (
            f"Looking for trusted {service} in {location}? Our team provides fast, reliable "
            f"{service} services for homeowners and businesses in the {location} area. "
            f"Contact us today to schedule an appointment or request a free estimate."
        ),
    }
