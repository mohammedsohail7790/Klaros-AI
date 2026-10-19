"""Content topics for a Medical Tourism business, written into its own knowledge base so the marketing crew has real material to draft from.

Everything here is process and checklist advice built only from what the owner told setup (the procedures and the business model): no prices, no results, no guarantees, no clinic or doctor
names, nothing about any patient. The owner can edit or add sections in `marketing/content-topics.md`; the crew only ever turns a section into a DRAFT that a person approves.
"""

from __future__ import annotations

import re

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


# ---- more topics, added automatically when the first set has all been drafted ----------------------------------------------------------------------------
# Same rules as above: process and checklist advice only. No prices, results, guarantees, rankings or names of clinics or doctors, and nothing about any patient.
_GENERIC = (
    ("How to read a medical travel quote", "A good quote lists every item so you can compare it line by line. Ask whether it includes the procedure, the surgeon's fee, anaesthesia, the hospital stay, medication, follow-up visits and any tests. Ask what is not included, such as transfers, hotels or treatment for complications. Ask which currency the quote is in and when it expires. Keep the written quote and compare it with at least one other before you decide."),
    ("Questions to ask a clinic's international patient team", "Ask who your coordinator will be and which languages they speak. Ask how quickly they reply and how you can reach them after you fly home. Ask how your records are shared and stored. Ask what happens if your plans change, such as the date or the procedure. Write down the answers and ask for the important ones in writing."),
    ("How to check a hospital's accreditation yourself", "A hospital can say it is accredited, so check it directly. Ask for the name of the accrediting body and the certificate. Then look the hospital up on that body's own website and check the dates and the scope of the accreditation, which may cover only part of the hospital. If you cannot find it, ask the hospital to explain."),
    ("How to check a surgeon's training and registration", "Ask for your surgeon's full name, specialty and years in practice. Ask which professional register or society they belong to and look them up on its website. Ask how often they perform your procedure and whether you can see a consent form and a pre-operative plan before you travel. Hospital accreditation does not certify an individual surgeon, so check the surgeon separately."),
    ("Preparing your records and photos for a consultation", "Gather the records a clinic is likely to ask for, such as your medical history, current medication, allergies, previous operations and any scans or dental records. Take clear, well-lit photographs if the clinic asks for them. Share records only after you have agreed who will see them and why. Keep your own copy of everything you send."),
    ("Travel insurance and what it should cover", "Ordinary travel insurance often excludes planned medical treatment, so read the policy carefully. Ask the insurer whether it covers complications from treatment abroad and the cost of an extended stay or an early flight home. Check the conditions for flying after surgery. Keep the policy number and the insurer's emergency contact with your travel documents."),
    ("Planning recovery and flights after treatment", "Ask your clinic how long you should stay near it after treatment and when it is safe to fly. Book flights that can be changed if your recovery takes longer. Plan help at home for your return, and agree how follow-up checks will happen once you are back. Choose accommodation that suits recovery, with easy access to the clinic."),
    ("What to ask about aftercare once you are home", "Ask who you should contact if you have a concern after you return, and how quickly they reply. Ask whether follow-up appointments can be done by video and what photos or information they will need. Ask who is responsible for care if you need treatment near home, and keep your records so a local doctor can see what was done."),
    ("Comparing two clinics fairly", "Compare clinics on the same things: the surgeon, the accreditation, what the quote includes, the aftercare plan and how clearly each one answers your questions. Be careful with a quote that is much lower than the others and ask what is left out. Do not decide on price alone. Take your time, and do not feel pressured to book quickly."),
    ("How your details are shared, and what you control", "You decide what is shared and with whom. Before a coordinator sends your details to a clinic, you should know which clinic, which details and why. You can change your mind or ask for your details to be removed. Ask any provider how long it keeps your records and who can see them."),
    ("Questions to ask about anaesthesia and the operating team", "Ask who will give the anaesthetic and what qualifications they hold. Ask who else will be in the operating team and who will look after you overnight. Ask what happens in an emergency and whether the hospital can transfer you to a larger hospital if needed."),
    ("A checklist for your first video consultation", "Prepare a short list of questions and your goals. Have your records and photos ready. Ask to speak with the surgeon who will operate, not only a coordinator. Ask what the surgeon needs to see and what cannot be assessed remotely. Note the answers and ask for a written plan afterwards."),
)
_PER_PROCEDURE = (
    ("What to ask about follow-up care after {p} abroad", "Ask how many follow-up visits the plan includes and where they take place. Ask how long you should stay near the clinic and when you can travel home. Ask how problems after you leave are handled, who pays for a revision and how to reach the team from home. Get the follow-up plan in writing before you book."),
    ("How to compare two {p} quotes", "Put the two quotes side by side and compare what each includes line by line: the procedure, materials or implants, the surgeon's fee, hospital or clinic charges, medication and follow-up. Check which currency each uses and what is left out. Ask each clinic to explain the differences, and ask about the exact products or techniques they plan to use."),
    ("Preparing for a {p} consultation", "Write down what you want and what worries you. Collect the records the clinic asks for and a list of your medication and allergies. Ask what the consultation will cover, who you will speak with and whether it is free. Ask what you need to decide before you book and how long the offer stays open."),
)
_PROCEDURE_TITLE = re.compile(r"^Questions to ask before choosing (.+) abroad$")


def library(procedures: list[str]) -> list[tuple[str, str]]:
    """The standard follow-on topics for a business offering these procedures, general ones first so the early drafts suit every reader."""
    out = list(_GENERIC)
    for title_tpl, body in _PER_PROCEDURE:
        out.extend((title_tpl.format(p=p), body) for p in procedures)
    return out


def missing_topics(existing_markdown: str, limit: int = 6) -> list[tuple[str, str]]:
    """Library topics whose title is not in the file yet. The procedures are read from the file's own 'Questions to ask before choosing X abroad' sections, so nothing else is needed."""
    existing = parse_topics(existing_markdown)
    have = {t.strip().lower() for t, _ in existing}
    procedures = [m.group(1) for t, _ in existing if (m := _PROCEDURE_TITLE.match(t))]
    return [(t, b) for t, b in library(procedures) if t.strip().lower() not in have][:limit]


def append_topics(existing_markdown: str, extra: list[tuple[str, str]]) -> str:
    """The file with the new sections added at the end. Everything the owner wrote or edited stays exactly as it was."""
    return (existing_markdown or "").rstrip() + "\n\n" + "\n\n".join(f"## {title}\n\n{body}" for title, body in extra) + "\n"
