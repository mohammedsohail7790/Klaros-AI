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
    LANGUAGE_UNSUPPORTED = "LANGUAGE_UNSUPPORTED"  # text this classifier cannot read: it cannot vouch for it, so a person reads it
    NORMAL_BUSINESS_INQUIRY = "NORMAL_BUSINESS_INQUIRY"


# Highest priority first.
_PRIORITY: tuple[SafetyCategory, ...] = (
    SafetyCategory.EMERGENCY,
    SafetyCategory.DIAGNOSIS_REQUEST,
    SafetyCategory.PRESCRIPTION_REQUEST,
    SafetyCategory.OUTCOME_GUARANTEE_REQUEST,
    SafetyCategory.UNKNOWN_PROVIDER_REQUEST,
    SafetyCategory.LANGUAGE_UNSUPPORTED,
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
    SafetyCategory.LANGUAGE_UNSUPPORTED: (
        "This text is in a language (or form) this system cannot screen for medical safety. Do not answer or qualify it automatically; "
        "hand it to a person who reads that language."
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
    SafetyCategory.LANGUAGE_UNSUPPORTED: SEVERITY_MEDIUM,
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
    "cardio_neuro_symptoms": _p(r"\bpalpitation(s)?\b", r"\bheart (is |was )?(racing|pounding|fluttering|skipping)\b", r"\b(dizzy|dizziness|light[- ]?headed|vertigo)\b", r"\bnumb(ness)?\b", r"\btingling\b",
                                r"\b(blurred|double|lost|losing) vision\b", r"\bslurred speech\b", r"\b(sudden|severe|worst) (headache|weakness|confusion)\b", r"\bconfus(ed|ion)\b"),
    "bleeding_vomiting": _p(r"\bvomit(ing|ed)?\b", r"\bcough(ing)? (up )?blood\b", r"\bblood (in|when|from)\b", r"\b(can'?t|cannot|unable to) (move|feel|see|speak|walk|stand)\b", r"\bblack(ed)? out\b"),
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



# ----------------------------------------------------------------------------------------------------------------------
# Languages other than English. Best-effort LEXICAL rules for the languages most common in medical-tourism enquiries (Arabic, Turkish, Russian,
# Spanish, French, German): unambiguous emergency terms plus diagnosis / prescription / guarantee requests. They are NOT a translation and NOT
# complete. The safety net is `LANGUAGE_UNSUPPORTED`: text in a script or language this classifier cannot read goes to a person.
# Matching is accent- and diacritic-insensitive (see `_fold`), applied to the pattern text and the patient text alike.
# ----------------------------------------------------------------------------------------------------------------------
_COMBINING = re.compile(r"[̀-ͯؐ-ًؚ-ٰٟۖ-ۭ]")
_ARABIC_MAP = str.maketrans({"ـ": None, "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_LATIN_EXTRA = str.maketrans({"ı": "i", "İ": "i", "ß": "ss", "ø": "o", "æ": "ae", "œ": "oe", "ł": "l", "đ": "d", "ё": "е"})


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", unicodedata.normalize("NFKC", text or "").lower())
    t = _COMBINING.sub("", t).translate(_ARABIC_MAP).translate(_LATIN_EXTRA)
    return re.sub(r"\s+", " ", t).strip()


def _pf(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(_fold(x), re.IGNORECASE) for x in patterns]


_ML_EMERGENCY = _pf(
    # Arabic
    r"الم (في )?الصدر", r"وجع (في )?الصدر", r"لا استطيع التنفس", r"(صعوبه|ضيق) (في )?التنفس", r"نوبه قلبيه", r"سكته (قلبيه|دماغيه)", r"فقدان الوعي", r"فاقد الوعي",
    r"اغمي عليه", r"نزيف (حاد|شديد)", r"جرعه زائده", r"تسمم", r"انتحار", r"اريد ان اموت", r"انهي حياتي", r"حاله طارئه", r"طوارئ", r"اسعاف",
    # Turkish
    r"gogus agri", r"nefes alamiyorum", r"nefes darligi", r"nefes almakta zorlan", r"kalp krizi", r"\bfelc\b", r"bilincini kaybet", r"\bbayildi", r"siddetli kanama", r"asiri doz",
    r"zehirlen", r"intihar", r"olmek istiyorum", r"\bacil\b", r"ambulans", r"olum(ü|u) ?yorum",
    # Russian
    r"боль в груди", r"не могу дышать", r"трудно дышать", r"затрудн[её]нное дыхание", r"сердечный приступ", r"инсульт", r"без сознания", r"потерял[аи]? сознание",
    r"сильное кровотечение", r"передозировк", r"отравлени", r"суицид", r"хочу умереть", r"скорую", r"скорая помощь", r"экстренн",
    # Spanish
    r"dolor (de|en el) pecho", r"no puedo respirar", r"dificultad para respirar", r"ataque (al|de) corazon", r"\binfarto\b", r"derrame cerebral", r"inconsciente", r"\bdesmayo\b",
    r"sangrado (severo|abundante|fuerte)", r"hemorragia", r"sobredosis", r"envenenamiento", r"suicid", r"quiero morir", r"emergencia", r"ambulancia",
    # French
    r"douleur (thoracique|a la poitrine|dans la poitrine)", r"je ne peux pas respirer", r"difficultes? (a|pour) respirer", r"crise cardiaque", r"\bavc\b", r"inconscient", r"evanoui",
    r"hemorragie", r"saignement (abondant|important|severe)", r"surdose", r"empoisonnement", r"suicide", r"je veux mourir", r"\burgence\b", r"ambulance",
    # German
    r"brustschmerz", r"schmerzen in der brust", r"kann nicht atmen", r"\batemnot\b", r"herzinfarkt", r"schlaganfall", r"bewusstlos", r"starke blutung", r"uberdosis", r"vergiftung",
    r"selbstmord", r"suizid", r"\bnotfall\b", r"krankenwagen",
)
_ML_DIAGNOSIS = _pf(
    r"هل (اعاني|عندي|لدي|عندى)", r"تشخيص", r"ما (هو )?مرضي", r"ماذا (يعني|تعني) (نتيجه|تحليل|اشعه)",
    r"\bteshis", r"hastaligim ne", r"kanser mi", r"bende .{0,30} var mi",
    r"диагноз", r"у меня рак", r"что со мной", r"это серьезно", r"это опасно",
    r"diagnostico", r"que enfermedad", r"tengo cancer", r"es grave", r"que significan mis (resultados|analisis)",
    r"diagnostic", r"quelle maladie", r"ai[- ]je un cancer", r"est[- ]ce grave", r"que signifient mes (resultats|analyses)",
    r"diagnose", r"habe ich krebs", r"ist es ernst", r"was bedeuten meine (ergebnisse|befunde|werte)",
)
_ML_PRESCRIPTION = _pf(
    r"وصفه طبيه", r"(اي|ما) دواء", r"جرعه", r"مضاد حيوي", r"مسكن(ات)?",
    r"\brecete", r"hangi ilac", r"\bdoz\b", r"antibiyotik", r"agri kesici",
    r"рецепт", r"какое лекарство", r"дозировк", r"антибиотик", r"обезболивающ",
    r"\breceta\b", r"que medicamento", r"\bdosis\b", r"antibiotico", r"analgesico",
    r"ordonnance", r"quel medicament", r"posologie", r"\bdosage\b", r"antibiotique",
    r"\brezept", r"welches medikament", r"dosierung", r"antibiotika", r"schmerzmittel",
)
_ML_GUARANTEE = _pf(
    r"ضمان", r"مضمون", r"اضمن", r"نسبه النجاح", r"بدون مخاطر", r"100 ?%",
    r"garanti", r"yuzde yuz", r"basari orani", r"risksiz", r"kesin(likle)? (basar|iyiles|sonuc)",
    r"гаранти", r"гарантия", r"без риска", r"процент успех", r"100 ?%",
    r"garantia", r"garantiz", r"sin riesgo", r"tasa de exito", r"100 ?%",
    r"garantie", r"garantir", r"sans risque", r"taux de reussite", r"100 ?%",
    r"garantie", r"garantieren", r"ohne risiko", r"erfolgsquote", r"erfolgsrate", r"100 ?%",
)

# Look-alike letters (Cyrillic / Greek used to dodge Latin rules), leetspeak digits and symbols. Used ONLY to build a second, "de-obfuscated" view of the
# text; the English rules run on both views, so genuine Russian or Arabic text is untouched in the first.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­‎‏‪‫‬‭‮"), None)
_HOMOGLYPH = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "у": "y", "і": "i", "ј": "j", "ѕ": "s", "ԁ": "d", "ɡ": "g", "н": "h", "к": "k", "т": "t", "м": "m", "в": "b",
                            "α": "a", "ο": "o", "ρ": "p", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "τ": "t", "υ": "u", "η": "n"})
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "|": "i"})
_SPACED = re.compile(r"\b(?:\w[ .\-_*,]){3,}\w\b")

_EN_STOPWORDS = frozenset(
    "the a an and or but to of in on at for with from by about as is are was were be been am i me my we our you your he she it they them this that these those there here "
    "have has had do does did can could will would should may might must not no yes please hello hi dear thanks thank want need like looking interested know tell how what when "
    "where which who why if so also too very just any some more most than then them its into out up down over after before since while".split()
)


# The few highest-severity English phrases, matched with every non-alphanumeric character removed so that "c h e s t   p a i n", "chest-pain" and
# "chest.pain" cannot dodge the word-boundary rules. A substring match can over-trigger; that only sends a message to a person.
_SQUASHED_EMERGENCY = (
    "chestpain", "chesttightness", "chestpressure", "cantbreathe", "cannotbreathe", "notbreathing", "heartattack", "cardiacarrest", "overdose", "suicid",
    "killmyself", "endmylife", "unresponsive", "anaphyla", "callanambulance", "callambulance", "needanambulance", "lifethreatening", "bleedingheavily", "bleedingbadly",
)


def _squashed_emergency(views: tuple[str, ...]) -> bool:
    return any(k in re.sub(r"[\W_]+", "", v) for v in views for k in _SQUASHED_EMERGENCY)


def _deobfuscate(t: str) -> str:
    t = t.translate(_ZERO_WIDTH).translate(_HOMOGLYPH)
    t = _SPACED.sub(lambda m: re.sub(r"[ .\-_*,]", "", m.group(0)), t).translate(_LEET)
    return re.sub(r"\s+", " ", t).strip()


def _is_latin_letter(ch: str) -> bool:
    try:
        return unicodedata.name(ch).startswith("LATIN")
    except ValueError:
        return False


def _language_uncertain(t: str) -> bool:
    """True when the text is in a script this classifier cannot read, or is a long passage that does not look like English. Short fragments (a service
    name, a city) are NOT flagged: they carry too little to judge and would otherwise send every terse lead to a person."""
    sample = t[:2000]
    letters = [c for c in sample if c.isalpha()]
    if not letters:
        return False
    if sum(1 for c in letters if not _is_latin_letter(c)) >= 3:
        return True
    words = re.findall(r"[a-z']+", _fold(sample))
    if len(words) >= 8 and sum(1 for w in words if w in _EN_STOPWORDS) / len(words) < 0.12:
        return True
    return False


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


def _any(patterns: Iterable[re.Pattern[str]], *texts: str) -> bool:
    return any(p.search(t) for p in patterns for t in texts)


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
    # Two views of the text: as written, and de-obfuscated (zero-width characters removed, spaced-out letters joined, look-alike letters and leetspeak
    # mapped back). The English rules run on both; the multilingual rules run on the accent-folded views.
    t0, t1 = t, _deobfuscate(t)
    views = (t0,) if t1 == t0 else (t0, t1)
    folded = tuple(dict.fromkeys(_fold(v) for v in views))

    for rid, pats in _EMERGENCY_CRITICAL.items():
        if _any(pats, *views):
            rules.setdefault(SafetyCategory.EMERGENCY, []).append(rid)
    if _any(policy.extra_patterns(), *views):
        rules.setdefault(SafetyCategory.EMERGENCY, []).append("business_emergency_term")
    if _squashed_emergency(views):
        rules.setdefault(SafetyCategory.EMERGENCY, []).append("emergency_obfuscated")
    if _any(_ML_EMERGENCY, *folded):
        rules.setdefault(SafetyCategory.EMERGENCY, []).append("emergency_multilingual")
    if SafetyCategory.EMERGENCY not in rules:
        for rid, pats in _EMERGENCY_REVIEW.items():
            if _any(pats, *views):
                rules.setdefault(SafetyCategory.EMERGENCY, []).append(rid)
                emergency_severity = SEVERITY_HIGH  # ambiguous: a person reviews soon, not the emergency script

    if _any(_DIAGNOSIS, *views) or _any(_ML_DIAGNOSIS, *folded):
        rules.setdefault(SafetyCategory.DIAGNOSIS_REQUEST, []).append("diagnosis_request")
    if _any(_PRESCRIPTION, *views) or _any(_ML_PRESCRIPTION, *folded):
        rules.setdefault(SafetyCategory.PRESCRIPTION_REQUEST, []).append("prescription_request")
    if _any(_GUARANTEE, *views) or _any(_ML_GUARANTEE, *folded):
        rules.setdefault(SafetyCategory.OUTCOME_GUARANTEE_REQUEST, []).append("outcome_guarantee")
    if _language_uncertain(t0):
        rules.setdefault(SafetyCategory.LANGUAGE_UNSUPPORTED, []).append("language_unsupported")

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
