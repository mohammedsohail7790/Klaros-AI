"""Content topics for a Medical Tourism business, written into its own knowledge base so the marketing crew has real material to draft from.

Everything here is process and checklist advice built only from what the owner told setup (the procedures and the business model): no prices, no results, no guarantees, no clinic or doctor
names, nothing about any patient. The owner can edit or add sections in `marketing/content-topics.md`; the crew only ever turns a section into a DRAFT that a person approves.
"""

from __future__ import annotations

TOPICS_PATH = "marketing/content-topics.md"

_QUESTIONS = (
    "Planning {procedure} abroad starts with the right questions. Ask for a written quote and exactly what it includes, such as follow-up visits and extra hospital nights. "
    "Ask who your surgeon will be, how long they have practised and which accreditation the hospital holds, then check it yourself with the issuing body. "
    "Ask what happens if you need a revision after you fly home, and who pays for it. Compare at least two clinics before you decide."
)
_MODEL = {
    "referral": "{business} introduces you to independent clinics and hospitals. We do not provide medical care and nothing here is medical advice. You decide at each step, and your details are shared with a clinic only after you agree.",
    "coordination": "{business} helps you coordinate the practical side of treatment abroad. We do not provide medical care and nothing here is medical advice. You decide at each step, and your details are shared only after you agree.",
    "both": "{business} introduces you to independent clinics and hospitals and helps you coordinate the practical side. We do not provide medical care and nothing here is medical advice. You decide at each step, and your details are shared with a clinic only after you agree.",
}
_TRIP = (
    "Before you travel for treatment, check that your passport is valid for the whole stay and read the visa rules for your nationality on the official government site. "
    "Arrange travel insurance that covers medical travel, and ask your clinic how long recovery usually takes so you can plan flights and time off. "
    "Choose accommodation close to the clinic if you can, and agree the follow-up plan in writing before you leave home."
)


def build_topics(business_name: str, business_model: str, procedures: list[str]) -> str:
    sections = [("How " + business_name + " works", _MODEL.get(business_model, _MODEL["referral"]).format(business=business_name))]
    for p in procedures[:20]:
        sections.append((f"Questions to ask before choosing {p} abroad", _QUESTIONS.format(procedure=p.lower())))
    sections.append(("Planning your trip around treatment", _TRIP))
    head = ("# Content topics\n\nEach section below can become a blog draft for your approval. Edit the wording or add your own sections: the marketing team only ever creates drafts, and you approve before anything is published. "
            "Keep to process and checklist advice. Do not add prices, results, guarantees or the names of clinics or doctors.\n\n")
    return head + "\n\n".join(f"## {title}\n\n{body}" for title, body in sections) + "\n"


def parse_topics(markdown: str) -> list[tuple[str, str]]:
    """The `## Title` sections of a topics file as (title, body), in order. Text before the first section is ignored."""
    out: list[tuple[str, str]] = []
    title: str | None = None
    body: list[str] = []
    for line in (markdown or "").splitlines():
        if line.startswith("## "):
            if title and " ".join(body).strip():
                out.append((title, " ".join(body).strip()))
            title, body = line[3:].strip(), []
        elif title is not None:
            body.append(line.strip())
    if title and " ".join(body).strip():
        out.append((title, " ".join(body).strip()))
    return [(t[:255], b) for t, b in out if t]
