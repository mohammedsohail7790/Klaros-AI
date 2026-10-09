"""Medical Tourism safety policy: a deterministic classifier for what a patient (or a message about a patient) says.

What this is, and is not
------------------------
This is Klaros' OWN safety gate. It runs on text Klaros receives or stores — the public enquiry form, a lead's
description, the summary of a conversation Halla reports back — and it decides, with fixed rules and no model call,
whether a person must look at it before anything else happens. It does NOT control what Halla's voice agents say
during a live call: Halla's workforce configuration has no safety-policy field, and Klaros cannot read Halla's agent
prompts. That gap is documented in docs/MEDICAL_TOURISM_PILOT.md and is a Halla-side dependency.

Design rules
------------
* Deterministic and conservative: fixed patterns, no LLM. When something is ambiguous the answer is "a person must
  review", never an invented answer.
* It never diagnoses, never recommends a medication, never promises an outcome. It only LABELS text.
* It never logs, stores or returns the text it was given. A result carries a category, a severity, rule ids and a
  response policy — nothing a patient wrote.
* Priority when several rules match: EMERGENCY > DIAGNOSIS_REQUEST > PRESCRIPTION_REQUEST > OUTCOME_GUARANTEE_REQUEST >
  UNKNOWN_PROVIDER_REQUEST > NORMAL_BUSINESS_INQUIRY. Price/availability questions are reported as `flags` (they are
  normal business enquiries whose answer may only come from verified business knowledge).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable


class SafetyCategory(StrEnum):
    EMERGENCY = "EMERGENCY"
    DIAGNOSIS_REQUEST = "DIAGNOSIS_REQUEST"
    PRESCRIPTION_REQUEST = "PRESCRIPTION_REQUEST"
    OUTCOME_GUARANTEE_REQUEST = "OUTCOME_GUARANTEE_REQUEST"
    UNKNOWN_PROVIDER_REQUEST = "UNKNOWN_PROVIDER_REQUEST"
    NORMAL_BUSINESS_INQUIRY = "NORMAL_BUSINESS_INQUIRY"


# Highest priority first.
_PRIORITY: tuple[SafetyCategory, ...] = (
    SafetyCategory.EMERGENCY,
    SafetyCategory.DIAGNOSIS_REQUEST,
    SafetyCategory.PRESCRIPTION_REQUEST,
    SafetyCategory.OUTCOME_GUARANTEE_REQUEST,
    SafetyCategory.UNKNOWN_PROVIDER_REQUEST,
)

# Severity of an EMERGENCY result: "critical" = clear emergency language; "high" = ambiguous symptom language that a
# person must review soon (conservative: we do not decide it is harmless).
SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_NONE = "critical", "high", "medium", "none"

DEFAULT_EMERGENCY_INSTRUCTION = (
    "If this is a medical emergency, contact your local emergency services immediately. "
    "A member of our team will follow up as soon as possible."
)

# What an automated agent or workflow may do for each category. These are instructions to the SYSTEM, not medical text.
_RESPONSE_POLICY: dict[SafetyCategory, str] = {
    SafetyCategory.EMERGENCY: (
        "Do not give medical advice. Direct the person to local emergency services now, hand over to a human at once, "
        "and stop sales qualification. Never discourage emergency care."
    ),
    SafetyCategory.DIAGNOSIS_REQUEST: (
        "Do not diagnose or interpret symptoms. Say a licensed clinician must assess this and offer to arrange a "
        "consultation through the provider; route to human review."
    ),
    SafetyCategory.PRESCRIPTION_REQUEST: (
        "Do not recommend, name, or adjust any medication or dose. Say only a licensed clinician can advise on "
        "medication; route to human review."
    ),
    SafetyCategory.OUTCOME_GUARANTEE_REQUEST: (
        "Do not promise or imply any treatment result, success rate or absence of risk. Explain that outcomes depend on "
        "the individual and the treating clinician; route to human review."
    ),
    SafetyCategory.UNKNOWN_PROVIDER_REQUEST: (
        "Only name providers, doctors or hospitals that exist in the verified provider directory. Never invent or "
        "recommend one; route to human review."
    ),
    SafetyCategory.NORMAL_BUSINESS_INQUIRY: (
        "Answer only from verified business knowledge. Never state a price, availability or provider detail that is not "
        "in it; if it is missing, say a team member will confirm and route to human review."
    ),
}

_SEVERITY: dict[SafetyCategory, str] = {
    SafetyCategory.EMERGENCY: SEVERITY_CRITICAL,
    SafetyCategory.DIAGNOSIS_REQUEST: SEVERITY_HIGH,
    SafetyCategory.PRESCRIPTION_REQUEST: SEVERITY_HIGH,
    SafetyCategory.OUTCOME_GUARANTEE_REQUEST: SEVERITY_MEDIUM,
    SafetyCategory.UNKNOWN_PROVIDER_REQUEST: SEVERITY_MEDIUM,
    SafetyCategory.NORMAL_BUSINESS_INQUIRY: SEVERITY_NONE,
}


def _p(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(x, re.IGNORECASE) for x in patterns]


# Clear emergency language. Matched against normalised text (lower-cased, apostrophes straightened).
_EMERGENCY_CRITICAL = {
    "chest_pain": _p(r"\bchest (pain|pains|tightness|pressure)\b"),
    "breathing": _p(r"\b(can'?t|cannot|unable to|can not) breathe\b", r"\bnot breathing\b", r"\b(difficulty|trouble) breathing\b", r"\bshort(ness)? of breath\b", r"\bgasping\b"),
    "cardiac_stroke": _p(r"\bheart attack\b", r"\bcardiac arrest\b", r"\bstroke\b", r"\bface (is )?drooping\b"),
    "unconscious": _p(r"\bunconscious\b", r"\bunresponsive\b", r"\bpassed out\b", r"\bfaint(ed|ing)\b", r"\bcollaps(ed|ing)\b"),
    "bleeding": _p(r"\b(severe|heavy|uncontrolled|won'?t stop) bleeding\b", r"\bbleeding (heavily|badly|a lot)\b", r"\bbleeding won'?t stop\b"),
    "overdose_poison": _p(r"\boverdos(e|ed|ing)\b", r"\bpoison(ed|ing)\b"),
    "self_harm": _p(r"\bsuicid(e|al)\b", r"\b(want|going|plan) to (die|kill myself|end my life)\b", r"\bkill myself\b", r"\bend my life\b", r"\bself[- ]harm\b"),
    "seizure_anaphylaxis": _p(r"\bseizure(s)?\b", r"\banaphyla(xis|ctic)\b", r"\bthroat (is )?(closing|swelling)\b", r"\bswollen throat\b"),
    "says_emergency": _p(r"\b(this is|it'?s|it is|is an?|medical|real|my|an) emergency\b", r"\bemergency (room|department|services)\b", r"\bcall (an )?ambulance\b", r"\b(need|get) (an )?ambulance\b", r"\bdying\b", r"\blife[- ]threatening\b"),
}

# Ambiguous symptom language: we do not decide it is harmless, so a person must review it soon.
_EMERGENCY_REVIEW = {
    "pain_swelling": _p(r"\b(severe|intense|unbearable|worsening|sharp) pain\b", r"\bpain (is )?(getting )?worse\b", r"\bswelling\b"),
    "infection_fever": _p(r"\binfect(ed|ion)\b", r"\bfever\b", r"\bpus\b", r"\bwound (is )?(open|opening|red)\b"),
    "post_procedure_problem": _p(r"\b(after|since) (my |the )?(surgery|operation|procedure|treatment)\b.{0,60}\b(pain|bleed|swell|fever|infect|problem|complication)", r"\bcomplication(s)?\b"),
    "urgent": _p(r"\b(very |extremely )?urgent(ly)?\b.{0,40}\b(medical|doctor|treatment|surgery|symptom)", r"\bneed (a doctor|treatment|surgery) (now|today|immediately|asap)\b"),
}

_DIAGNOSIS = _p(
    r"\bdo i have\b", r"\bwhat('?s| is) wrong with me\b", r"\bwhat('?s| is) wrong with my\b", r"\bdiagnos(e|is|ing)\b",
    r"\bwhat (condition|disease|illness) (do|could|might)\b", r"\b(is|could) (it|this|that) (be )?(cancer|a tumou?r|serious|dangerous|something serious)\b",
    r"\bwhat do(es)? my (symptoms?|results?|scans?|tests?|x-?rays?) mean\b", r"\bam i (sick|ill)\b", r"\bwhat (is|are) (causing|the cause of) my\b",
    r"\binterpret (my )?(results?|scans?|tests?|x-?rays?)\b",
)

_PRESCRIPTION = _p(
    r"\bprescri(be|bed|ption|ptions)\b", r"\bwhat (medication|medicine|drug|drugs|pill|pills|painkiller|antibiotic)s?\b",
    r"\bshould i (take|stop taking|increase|decrease)\b", r"\bcan i (take|mix|combine)\b.{0,50}\b(mg|ml|tablet|pill|medication|medicine|drug|antibiotic|painkiller)\b",
    r"\b(dose|dosage|dosing)\b", r"\bhow (much|many) .{0,30}\b(mg|ml|tablets?|pills?)\b", r"\b(antibiotics?|painkillers?|steroids?|opioids?)\b",
    r"\bstop (taking )?my (meds?|medication|medicine)\b",
)

_GUARANTEE = _p(
    r"\bguarantee(d|s)?\b", r"\b100 ?%\b", r"\bpromise (me|that|it)\b", r"\b(definitely|certainly|surely) (work|cure|fix|heal|succeed)\b",
    r"\bwill (it|this|the surgery|the procedure|the treatment) (definitely|certainly|surely|always) \w+", r"\bwill i be (cured|healed|fine|ok(ay)?|perfect)\b",
    r"\b(no|zero|without any) risk\b", r"\brisk[- ]free\b", r"\bsuccess rate\b", r"\bwhat are the chances\b", r"\bis it (safe|completely safe)\b",
)

_PROVIDER_GENERIC = _p(
    r"\b(best|top|number one|#1|finest|safest|cheapest) (doctor|surgeon|hospital|clinic|dentist|specialist)\b",
    r"\b(recommend|suggest|which|who is) (me )?(a |the |an )?(good |best )?(doctor|surgeon|hospital|clinic|dentist|specialist)\b",
    r"\bwho (will|would) (operate|perform|treat)\b",
)
_PROVIDER_NAMED = re.compile(r"\b(dr\.?|doctor|prof\.?|professor)\s+([a-z][a-z'\-]+(?:\s+[a-z][a-z'\-]+)?)\b", re.IGNORECASE)
_HOSPITAL_NAMED = re.compile(r"\b([a-z][a-z'\-]+(?:\s+[a-z][a-z'\-]+){0,3})\s+(hospital|clinic|medical cent(?:er|re))\b", re.IGNORECASE)

_PRICE = _p(r"\b(price|prices|pricing|cost|costs|how much|quote|quotation|fee|fees)\b")
_AVAILABILITY = _p(r"\bavailab(le|ility)\b", r"\b(earliest|next) (slot|appointment|date|opening)\b", r"\bwhen can (i|we|he|she) (come|start|be seen|have)\b", r"\bany (slot|opening)s?\b")

# Words that are not names when they follow "Dr"/"doctor" or precede "hospital".
_NOT_A_NAME = {"a", "an", "the", "any", "your", "my", "our", "which", "what", "who", "good", "best", "top", "this", "that", "private", "public", "local", "nearest", "another", "other"}


def _normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True)
class SafetyPolicy:
    """Configurable parts of the classifier. Defaults are conservative; a business may ADD emergency terms and replace the
    instruction text (e.g. with its own verified emergency number). It can never remove a built-in rule."""

    extra_emergency_terms: tuple[str, ...] = ()
    emergency_instruction: str = DEFAULT_EMERGENCY_INSTRUCTION

    def extra_patterns(self) -> list[re.Pattern[str]]:
        out: list[re.Pattern[str]] = []
        for term in self.extra_emergency_terms:
            term = _normalise(term)
            if 2 <= len(term) <= 80:
                out.append(re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE))
        return out


DEFAULT_POLICY = SafetyPolicy()


@dataclass(frozen=True)
class SafetyResult:
    category: SafetyCategory
    matched: bool  # True when anything other than a normal enquiry was found
    severity: str
    requires_human: bool
    safe_response_policy: str
    matched_rules: tuple[str, ...] = ()  # rule ids only — never the patient's words
    categories: tuple[SafetyCategory, ...] = ()  # every category that matched, highest priority first
    flags: tuple[str, ...] = field(default_factory=tuple)  # e.g. "price_question", "availability_question"
    emergency_instruction: str | None = None

    @property
    def is_emergency(self) -> bool:
        return self.category == SafetyCategory.EMERGENCY


def _any(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def classify(
    text: str | None,
    *,
    policy: SafetyPolicy = DEFAULT_POLICY,
    known_provider_names: Iterable[str] = (),
) -> SafetyResult:
    """Classify one piece of text. Pure, deterministic, side-effect free; never logs or returns the text.

    `known_provider_names`: names in the tenant's verified provider directory. A named doctor/hospital that is not in
    it is an UNKNOWN_PROVIDER_REQUEST (a person verifies it); without a directory every named provider is unknown.
    """
    t = _normalise(text or "")
    if not t:
        return _result(SafetyCategory.NORMAL_BUSINESS_INQUIRY, (), (), (), policy)

    rules: dict[SafetyCategory, list[str]] = {}
    emergency_severity = SEVERITY_CRITICAL

    for rid, pats in _EMERGENCY_CRITICAL.items():
        if _any(pats, t):
            rules.setdefault(SafetyCategory.EMERGENCY, []).append(rid)
    if _any(policy.extra_patterns(), t):
        rules.setdefault(SafetyCategory.EMERGENCY, []).append("business_emergency_term")
    if SafetyCategory.EMERGENCY not in rules:
        for rid, pats in _EMERGENCY_REVIEW.items():
            if _any(pats, t):
                rules.setdefault(SafetyCategory.EMERGENCY, []).append(rid)
                emergency_severity = SEVERITY_HIGH  # ambiguous: a person reviews soon, not the emergency script

    if _any(_DIAGNOSIS, t):
        rules.setdefault(SafetyCategory.DIAGNOSIS_REQUEST, []).append("diagnosis_request")
    if _any(_PRESCRIPTION, t):
        rules.setdefault(SafetyCategory.PRESCRIPTION_REQUEST, []).append("prescription_request")
    if _any(_GUARANTEE, t):
        rules.setdefault(SafetyCategory.OUTCOME_GUARANTEE_REQUEST, []).append("outcome_guarantee")

    known = {_normalise(n) for n in known_provider_names if n}
    unknown_provider = _any(_PROVIDER_GENERIC, t)
    def _is_known(span: str) -> bool:
        return any(k and (k in span or span in k) for k in known)

    for m in _PROVIDER_NAMED.finditer(t):
        name = m.group(2).strip()
        first = name.split()[0] if name else ""
        if first and first not in _NOT_A_NAME and not _is_known(m.group(0)) and not _is_known(name):
            unknown_provider = True
    for m in _HOSPITAL_NAMED.finditer(t):
        words = [w for w in m.group(1).strip().split() if w not in _NOT_A_NAME]
        if words and not _is_known(m.group(0)) and not _is_known(" ".join(words) + " " + m.group(2).lower()):
            unknown_provider = True
    if unknown_provider:
        rules.setdefault(SafetyCategory.UNKNOWN_PROVIDER_REQUEST, []).append("unknown_provider")

    flags: list[str] = []
    if _any(_PRICE, t):
        flags.append("price_question")
    if _any(_AVAILABILITY, t):
        flags.append("availability_question")

    ordered = tuple(c for c in _PRIORITY if c in rules)
    if not ordered:
        return _result(SafetyCategory.NORMAL_BUSINESS_INQUIRY, (), (), tuple(flags), policy)
    top = ordered[0]
    severity = emergency_severity if top == SafetyCategory.EMERGENCY else _SEVERITY[top]
    return _result(top, tuple(rules[top]), ordered, tuple(flags), policy, severity=severity)


def _result(
    category: SafetyCategory,
    matched_rules: tuple[str, ...],
    categories: tuple[SafetyCategory, ...],
    flags: tuple[str, ...],
    policy: SafetyPolicy,
    *,
    severity: str | None = None,
) -> SafetyResult:
    normal = category == SafetyCategory.NORMAL_BUSINESS_INQUIRY
    return SafetyResult(
        category=category,
        matched=not normal,
        severity=severity or _SEVERITY[category],
        requires_human=not normal,  # every non-normal category goes to a person
        safe_response_policy=_RESPONSE_POLICY[category],
        matched_rules=matched_rules,
        categories=categories,
        flags=flags,
        emergency_instruction=policy.emergency_instruction if category == SafetyCategory.EMERGENCY else None,
    )


def classify_many(texts: Iterable[str | None], *, policy: SafetyPolicy = DEFAULT_POLICY, known_provider_names: Iterable[str] = ()) -> SafetyResult:
    """The most serious classification across several fields (e.g. a lead's description and service)."""
    names = tuple(known_provider_names)
    worst: SafetyResult | None = None
    flags: set[str] = set()
    for t in texts:
        r = classify(t, policy=policy, known_provider_names=names)
        flags.update(r.flags)
        if worst is None or _rank(r) < _rank(worst):
            worst = r
    base = worst or classify("", policy=policy)
    if flags and set(base.flags) != flags:
        return SafetyResult(**{**base.__dict__, "flags": tuple(sorted(flags))})
    return base


def _rank(r: SafetyResult) -> tuple[int, int]:
    order = {c: i for i, c in enumerate(_PRIORITY)}
    sev = {SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 1, SEVERITY_MEDIUM: 2, SEVERITY_NONE: 3}
    return (order.get(r.category, len(_PRIORITY)), sev.get(r.severity, 3))
