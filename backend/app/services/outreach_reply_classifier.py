"""Reads a clinic's reply to an outreach email and says what it is. Pure, deterministic, no I/O and no model.

Precedence (first match wins): opt-out, bounce, auto-reply, handoff, not interested, then the answerable labels. Anything that matches nothing, matches two
answerable labels at once, or is otherwise unclear comes back as OTHER with low confidence, which hands off to a person.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.services.outreach_blocks import normalize_text

CONFIDENCE_THRESHOLD = 0.6
MAX_CHARS = 5000


class ReplyLabel(StrEnum):
    OPT_OUT = "OPT_OUT"
    BOUNCE = "BOUNCE"
    AUTO_REPLY = "AUTO_REPLY"
    HANDOFF = "HANDOFF"
    NOT_INTERESTED = "NOT_INTERESTED"
    ASKS_TERMS = "ASKS_TERMS"
    ASKS_DOCS = "ASKS_DOCS"
    ASKS_WHO = "ASKS_WHO"
    INTERESTED = "INTERESTED"
    OTHER = "OTHER"


@dataclass(frozen=True)
class Classification:
    label: ReplyLabel
    confidence: float
    reasons: tuple[str, ...]
    handoff_reason: str | None = None


def _any(*patterns: str) -> re.Pattern:
    return re.compile("|".join(patterns))


_OPT_OUT = _any(
    r"\bunsubscribe", r"\bremove (?:me|us)\b", r"\btake (?:me|us) off\b", r"\bstop (?:emailing|e-mailing|contacting|sending|writing|messaging)\b",
    r"\b(?:do not|don't|dont) (?:contact|email|e-mail|write to|message)\b", r"\bno more (?:emails?|e-mails?|messages?)\b", r"\bopt[\s-]?out\b",
    r"\bplease stop\b", r"^stop\W*$", r"\bdelete (?:my|our) (?:email|e-mail|address|details)\b",
    r"\bdarse de baja\b", r"\bdesuscrib", r"\bd[ée]sabonner\b", r"\bd[ée]sinscri", r"\babmelden\b", r"\baustragen\b", r"\blistemizden\b", r"\babonelikten\b",
    r"\bannulla l'iscrizione\b", r"\bcancelar (?:la )?suscripci",
)
_BOUNCE = _any(
    r"\bmailer-daemon\b", r"\bdelivery status notification\b", r"\bundeliverable\b", r"\baddress not found\b", r"\buser unknown\b",
    r"\bmailbox (?:is )?(?:full|unavailable|not found|does not exist)\b", r"\bcould not be delivered\b", r"\bdelivery (?:has )?failed\b", r"\bno such user\b",
    r"\brecipient address rejected\b",
)
_AUTO = _any(
    r"\bout[\s-]of[\s-]the[\s-]office\b", r"\bout[\s-]of[\s-]office\b", r"\bautomatic reply\b", r"\bauto[\s-]?reply\b", r"\bautomated (?:reply|response|message)\b",
    r"\bi am (?:currently )?away\b", r"\bi will be (?:out|away)\b", r"\bon (?:annual )?leave\b", r"\bcurrently (?:away|out)\b", r"\bdo not reply to this\b",
)
_HANDOFF: tuple[tuple[str, re.Pattern], ...] = (
    ("it mentions a legal matter", _any(r"\blawyer", r"\battorney", r"\blegal\b", r"\blawsuit", r"\bsue\b", r"\bsolicitor", r"\bgdpr\b", r"\bdata protection\b", r"\bcontract\b")),
    ("it is a complaint or threat", _any(r"\bcomplain", r"\breport (?:you|this)\b", r"\bspam\b", r"\bharass", r"\billegal\b", r"\bpolice\b", r"\bmisleading\b", r"\bscam\b")),
    ("it mentions a regulator or authority", _any(r"\bregulator", r"\bministry\b", r"\bauthorit(?:y|ies)\b", r"\bdata commissioner\b")),
    ("it is a press enquiry", _any(r"\bjournalist", r"\bpress (?:enquiry|inquiry|office|team)\b", r"\bnewspaper\b", r"\bmedia (?:enquiry|inquiry|team)\b")),
    ("it asks for patient information or raises a medical question", _any(
        r"\bpatient (?:information|data|details|records?|files?|list)\b", r"\bmedical records?\b", r"\bsend (?:us )?(?:the |your )?patients?\b", r"\bdiagnos", r"\bsymptom",
        r"\bwhich (?:treatment|procedure) (?:should|would)\b", r"\bmedical advice\b")),
    ("it is a price negotiation", _any(r"\bnegotiat", r"\bdiscount", r"\bcounter[\s-]?(?:offer|proposal)\b", r"\bhigher (?:rate|commission|fee|percentage)\b",
                                       r"\blower (?:rate|commission|fee|percentage)\b", r"\btoo (?:high|low)\b", r"\bbetter (?:rate|terms|deal)\b")),
    ("it states a figure or amount", _any(r"\d", r"%", r"\bper[\s-]?cent\b", r"[$€£¥₹]")),
)
_NOT_INTERESTED = _any(
    r"\bnot interested\b", r"\bno,? thank", r"\bwe (?:must )?decline\b", r"\bnot looking\b", r"\b(?:do not|don't|dont) need\b", r"\bnot a (?:good )?fit\b", r"\bno interest\b",
    r"\bnot at this time\b", r"\bnot able to (?:help|participate)\b", r"\bnot (?:currently )?accepting\b",
)
_TERMS = _any(r"\bcommission", r"\breferral fee", r"\bwhat do you charge\b", r"\bhow much\b", r"\bpricing\b", r"\bpayment terms\b", r"\byour terms\b", r"\bthe terms\b", r"\bfees?\b")
_DOCS = _any(r"\bmore (?:information|details|info)\b", r"\bsend (?:us |me )?(?:details|information|info|documents|a brochure|brochure)\b", r"\bwhat (?:do you need|documents|information)\b",
             r"\brequirements\b", r"\bhow does (?:it|this) work\b", r"\bhow do you work\b", r"\bbrochure\b")
_WHO = _any(r"\bwho are you\b", r"\bwho is this\b", r"\bwhat (?:is|are) (?:your company|you)\b", r"\btell (?:me|us) (?:more )?about (?:your|the) (?:company|business|service)\b",
            r"\bwhich company\b", r"\bwhat company\b", r"\bwho do you represent\b")
_INTERESTED = _any(r"\binterested\b", r"\bhappy to\b", r"\bsounds good\b", r"\bplease send\b", r"\blet'?s (?:talk|speak|connect)\b", r"\bcall (?:me|us)\b", r"\bwe would like\b", r"\bwe'd like\b",
                   r"\bkeen\b", r"\bpleased to\b", r"\bgladly\b", r"^yes\W*$", r"^sure\W*$")


def classify(text: str) -> Classification:
    t = normalize_text((text or "")[:MAX_CHARS]).strip()
    if not t:
        return Classification(ReplyLabel.OTHER, 0.0, ("the reply is empty",), "the reply is empty")
    if _OPT_OUT.search(t):
        return Classification(ReplyLabel.OPT_OUT, 1.0, ("asks to stop being contacted",))
    if _BOUNCE.search(t):
        return Classification(ReplyLabel.BOUNCE, 0.95, ("looks like a delivery failure notice",))
    if _AUTO.search(t):
        return Classification(ReplyLabel.AUTO_REPLY, 0.95, ("looks like an automatic reply",))
    for reason, pattern in _HANDOFF:
        if pattern.search(t):
            return Classification(ReplyLabel.HANDOFF, 0.95, (reason,), reason[0].upper() + reason[1:])
    if _NOT_INTERESTED.search(t):
        return Classification(ReplyLabel.NOT_INTERESTED, 0.95, ("says they are not interested",))
    hits = [(label, why) for label, pattern, why in (
        (ReplyLabel.ASKS_TERMS, _TERMS, "asks about commission or terms"),
        (ReplyLabel.ASKS_DOCS, _DOCS, "asks for information or documents"),
        (ReplyLabel.ASKS_WHO, _WHO, "asks who we are"),
    ) if pattern.search(t)]
    if len(hits) > 1:
        return Classification(ReplyLabel.OTHER, 0.5, tuple(w for _, w in hits), "It asks several different things at once")
    if hits:
        return Classification(hits[0][0], 0.9, (hits[0][1],))
    if _INTERESTED.search(t):
        return Classification(ReplyLabel.INTERESTED, 0.8, ("expresses interest",))
    return Classification(ReplyLabel.OTHER, 0.3, ("nothing in the reply matches a known request",), "We could not tell what the reply is asking")
