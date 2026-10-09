"""Dropshipping safety / customer-protection policy: a deterministic classifier for what a customer (or a message about a customer) says.

Scope, stated plainly
---------------------
This is Klaros' OWN gate on text Klaros receives or stores — an enquiry, a lead description, the summary of a conversation Halla reports back. It
decides, with fixed rules and no model call, whether a person must look at something before anything is promised. It does NOT control what Halla's
voice agents say on a live call (Halla's workforce configuration has no safety-policy field, and Klaros cannot read or set its agent prompts).

Rules
-----
* Deterministic and conservative; ambiguous or high-risk language goes to a person. It never invents an answer.
* The assistant may never state a price, a discount, stock, a delivery date/promise, a product specification, supplier information, a refund
  decision or a payment status that is not in verified business data. This module labels requests that ask for such inventions (FABRICATION_REQUEST,
  DELIVERY_GUARANTEE_REQUEST) and labels ordinary questions about them with `flags` so the answer is taken from verified data or escalated.
* It never logs, stores or returns the text it reads: a result carries a category, a severity, rule ids and a response policy.
* Priority when several rules match: CHARGEBACK > FRAUD_INDICATOR > PAYMENT_DISPUTE > REFUND_DISPUTE > PROHIBITED_PRODUCT >
  DELIVERY_GUARANTEE_REQUEST > FABRICATION_REQUEST > AMBIGUOUS_HIGH_RISK > NORMAL_BUSINESS_INQUIRY.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable


class CommerceSafetyCategory(StrEnum):
    CHARGEBACK = "CHARGEBACK"
    FRAUD_INDICATOR = "FRAUD_INDICATOR"
    PAYMENT_DISPUTE = "PAYMENT_DISPUTE"
    REFUND_DISPUTE = "REFUND_DISPUTE"
    PROHIBITED_PRODUCT = "PROHIBITED_PRODUCT"
    DELIVERY_GUARANTEE_REQUEST = "DELIVERY_GUARANTEE_REQUEST"
    FABRICATION_REQUEST = "FABRICATION_REQUEST"
    AMBIGUOUS_HIGH_RISK = "AMBIGUOUS_HIGH_RISK"
    NORMAL_BUSINESS_INQUIRY = "NORMAL_BUSINESS_INQUIRY"


C = CommerceSafetyCategory

_PRIORITY: tuple[CommerceSafetyCategory, ...] = (
    C.CHARGEBACK, C.FRAUD_INDICATOR, C.PAYMENT_DISPUTE, C.REFUND_DISPUTE, C.PROHIBITED_PRODUCT,
    C.DELIVERY_GUARANTEE_REQUEST, C.FABRICATION_REQUEST, C.AMBIGUOUS_HIGH_RISK,
)

SEVERITY_CRITICAL, SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_NONE = "critical", "high", "medium", "none"
_SEVERITY: dict[CommerceSafetyCategory, str] = {
    C.CHARGEBACK: SEVERITY_CRITICAL, C.FRAUD_INDICATOR: SEVERITY_CRITICAL, C.PAYMENT_DISPUTE: SEVERITY_HIGH, C.REFUND_DISPUTE: SEVERITY_HIGH,
    C.PROHIBITED_PRODUCT: SEVERITY_HIGH, C.DELIVERY_GUARANTEE_REQUEST: SEVERITY_MEDIUM, C.FABRICATION_REQUEST: SEVERITY_MEDIUM,
    C.AMBIGUOUS_HIGH_RISK: SEVERITY_HIGH, C.NORMAL_BUSINESS_INQUIRY: SEVERITY_NONE,
}

# Instructions to the SYSTEM (an agent or a workflow), never customer-facing text.
_RESPONSE_POLICY: dict[CommerceSafetyCategory, str] = {
    C.CHARGEBACK: "Do not argue or make any payment, refund or order-status statement. Acknowledge, stop automated replies on this matter and hand over to a person.",
    C.FRAUD_INDICATOR: "Do not process, confirm or ship anything. Do not accuse the customer. Hand over to a person for review.",
    C.PAYMENT_DISPUTE: "Never state or guess a payment status. Hand over to a person who can check the payment records.",
    C.REFUND_DISPUTE: "Never promise, deny or decide a refund. Hand over to a person; only a person decides refunds.",
    C.PROHIBITED_PRODUCT: "Do not offer, source or discuss supplying this. Hand over to a person; unsupported or prohibited products are not sold.",
    C.DELIVERY_GUARANTEE_REQUEST: "Never guarantee or promise a delivery date. Give only the verified delivery window for the product and destination, or say a person will confirm; route to a person.",
    C.FABRICATION_REQUEST: "Refuse to state any product specification, stock level, delivery date, discount or price that is not in verified business data. Say a person will confirm; route to a person.",
    C.AMBIGUOUS_HIGH_RISK: "Do not continue automatically. Hand over to a person.",
    C.NORMAL_BUSINESS_INQUIRY: (
        "Answer only from verified business data (catalogue, policies). Never state a price, discount, stock level, delivery date, specification, supplier detail, "
        "refund decision or payment status that is not recorded there; if it is missing, say a team member will confirm and route to a person."
    ),
}


def _p(*patterns: str) -> list[re.Pattern[str]]:
    return [re.compile(x, re.IGNORECASE) for x in patterns]


_RULES: dict[CommerceSafetyCategory, dict[str, list[re.Pattern[str]]]] = {
    C.CHARGEBACK: {
        "chargeback": _p(r"\bcharge[- ]?backs?\b"),
        "dispute_with_bank": _p(r"\bdispute (the |this |that |my )?(charge|payment|transaction)\b", r"\b(call|contact|tell|report (this )?to) (my|the) (bank|card (issuer|company))\b", r"\breverse the (charge|payment|transaction)\b", r"\bcredit card company\b.{0,40}\b(dispute|claim)\b"),
    },
    C.FRAUD_INDICATOR: {
        "fraud_words": _p(r"\bfraud(ulent|ster)?\b", r"\bscam(med|mer|s)?\b", r"\bcarding\b"),
        "stolen_or_unauthorised": _p(r"\bstolen (credit )?card\b", r"\bcard (is |was |has been )?stolen\b", r"\bunauthori[sz]ed (charge|transaction|payment|order)\b", r"\bsomeone (else )?(used|has used) my (card|account|name)\b", r"\b(use|using) (someone else'?s|another person'?s|a friend'?s) (credit )?card\b"),
        "evasion": _p(r"\bfake (order|id|identity|name|address)\b", r"\b(ship|send) (it )?to (a )?(freight|forwarder|reshipper|re-?shipper)\b", r"\bdifferent (name|address) (than|from) (the )?(card|billing)\b", r"\bdon'?t (verify|check) (my )?(id|identity|card)\b"),
    },
    C.PAYMENT_DISPUTE: {
        "double_charge": _p(r"\bcharged (me )?(twice|two times|double|again|multiple times)\b", r"\bdouble[- ]charged\b", r"\bcharged (me )?the wrong amount\b", r"\bwrong amount\b.{0,30}\b(charged|payment|paid)\b"),
        "payment_missing": _p(r"\bpayment (didn'?t|did not|failed|not (go|went) through|is missing|disappeared)\b", r"\b(paid|charged) (but|and) (no|never|didn'?t|did not|nothing)\b", r"\bmoney (was |has been )?(taken|deducted|withdrawn)\b", r"\bwhere is my payment\b"),
    },
    C.REFUND_DISPUTE: {
        "refund_not_received": _p(r"\brefund (still )?(hasn'?t|has not|not|never) (arrived|come|been (received|issued|processed|paid))\b", r"\b(didn'?t|never|still haven'?t|have not|hasn'?t) (get|got|receive|received|gotten) (my |their |the |his |her )?(refund|money back)\b"),
        "refund_refused": _p(r"\byou (refused|denied|rejected|ignored) (my )?(refund|return|request)\b", r"\bdemand (a |my |the )?(full )?(refund|money back)\b", r"\bpromised (me )?(a )?refund\b", r"\bwhere is my (refund|money)\b"),
    },
    C.PROHIBITED_PRODUCT: {
        "prohibited": _p(
            r"\b(fire ?arms?|guns?|pistols?|rifles?|ammunition|ammo|explosives?|grenades?|weapons?)\b", r"\bcounterfeit(s|ed)?\b", r"\b(replica|knock-?off|fake) (of )?(a )?(designer|brand|rolex|gucci|louis vuitton|nike)\b",
            r"\bfake (passports?|ids?|documents?|licen[sc]es?|diplomas?)\b", r"\b(cocaine|heroin|meth(amphetamine)?|fentanyl|illegal drugs?|narcotics?)\b", r"\bstolen (goods|items|products)\b",
            r"\b(ivory|endangered species|human organs?)\b", r"\bprescription (drugs?|medication|pills)\b",
        ),
    },
    C.DELIVERY_GUARANTEE_REQUEST: {
        "guarantee_delivery": _p(
            r"\bguarantee(d|s)?\b.{0,40}\b(deliver\w*|arriv\w*|by (monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|next week)|on time|ship\w*)\b",
            r"\b(deliver\w*|arriv\w*|get here|ship\w*)\b.{0,30}\bguarantee", r"\bpromise (me )?(that )?(it|delivery|you will deliver|to deliver)\b",
            r"\bwill (it|this|the order|the package) (definitely|certainly|surely) (arrive|be delivered|get here)\b", r"\b(100 ?%|for sure|without fail)\b.{0,20}\b(arrive|deliver\w*|on time)\b",
        ),
    },
    C.FABRICATION_REQUEST: {
        "fabricate_spec": _p(
            r"\b(just |please )?(say|tell (the |that |our )?(me|them|him|her|customers?|clients?|buyers?)|write|claim|state|pretend|put) (that )?(it'?s|it is|they are|this is)? ?(waterproof|certified|ce[- ]?certified|fda[- ]approved|organic|genuine|authentic|original|non[- ]?toxic|hypoallergenic|lifetime warranty)\b",
            r"\b(make up|invent|fabricate|fake|guess) (the |some )?(specs?|specifications?|details|features|measurements|dimensions|materials?)\b",
        ),
        "fabricate_stock": _p(
            r"\b(say|tell (the |that |our )?(me|them|him|her|customers?|clients?|buyers?)|claim|pretend|write) (that )?(it'?s|it is|they are) (in stock|available)\b",
            r"\b(just |please )?(say|tell (the |that |our )?(me|them|him|her|customers?|clients?|buyers?)|claim|pretend|put|write) (that )?(we have|it'?s in stock|there is|there are|plenty|lots)\b.{0,30}\b(stock|available|units|pieces|plenty|lots|\d+)\b",
            r"\b(make up|invent|fabricate|fake|guess) (the |some )?(stock|inventory|availability)\b",
        ),
        "fabricate_delivery_date": _p(
            r"\b(make up|invent|fabricate|pick|just (say|give|tell)) (me )?(a |any |some )?(delivery|arrival|shipping|ship) (date|time|window|estimate)\b",
            r"\bgive me (a|any) (delivery )?date\b.{0,25}\b(even if|whatever|any date|doesn'?t matter)\b",
        ),
        "fabricate_discount": _p(
            r"\b(give|offer|do|apply) me (a |an )?\d{1,3} ?% (off|discount)\b", r"\b(make up|invent|create|fabricate) (a |some )?(discount|coupon|promo(tion)?( code)?|special price|price)\b",
            r"\bapply (a |the )?(secret|special|hidden|employee|staff|friends?)( and family)?( \w+)? (discount|code|price)\b", r"\b(match|beat) (any )?(price|competitor)\b.{0,20}\b(without|no need to) (ask|check|approv)",
        ),
    },
    C.AMBIGUOUS_HIGH_RISK: {
        "legal_threat": _p(r"\blegal action\b", r"\b(my |a )?(lawyer|attorney|solicitor)\b", r"\bsue (you|the company|your company)\b", r"\b(trading standards|consumer protection|ombudsman|attorney general)\b", r"\breport (you|this|the company) to\b", r"\bthe police\b"),
        "public_threat": _p(r"\b(post|tell|warn) (this |everyone |people )?(online|on social media|everywhere)\b", r"\bleave (a )?(bad|negative|1[- ]star) reviews? (everywhere|on every)\b", r"\bdestroy your (reputation|business)\b"),
    },
}

_FLAGS: dict[str, list[re.Pattern[str]]] = {
    "price_question": _p(r"\b(price|prices|pricing|cost|costs|how much|quote)\b"),
    "stock_question": _p(r"\b(in stock|out of stock|availab(le|ility)|how many (do you have|are left|left)|restock)\b"),
    "delivery_question": _p(r"\b(deliver\w*|shipping|ship to|arrive|arrival|how long|when (will|would|can|does)|tracking|track my)\b"),
    "discount_question": _p(r"\b(discount|coupon|promo(tion)?( code)?|sale|offer|deal|free shipping|bulk (price|discount))\b"),
    "spec_question": _p(r"\b(spec(s|ification)?s?|dimensions?|size|weight|material|compatib\w+|warranty|battery|capacity|does it (work|fit|come with))\b"),
    "return_question": _p(r"\b(return|returns|refund policy|send (it )?back|exchange)\b"),
    "order_status_question": _p(r"\b(where is my (order|package|parcel)|order status|has (my|the) order (shipped|arrived))\b"),
}


def _normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True)
class CommercePolicy:
    """Configurable parts. A business may ADD prohibited-product terms; it can never remove a built-in rule."""

    extra_prohibited_terms: tuple[str, ...] = ()

    def extra_patterns(self) -> list[re.Pattern[str]]:
        out: list[re.Pattern[str]] = []
        for term in self.extra_prohibited_terms:
            term = _normalise(term)
            if 2 <= len(term) <= 80:
                out.append(re.compile(r"\b" + re.escape(term) + r"\b", re.IGNORECASE))
        return out


DEFAULT_POLICY = CommercePolicy()


@dataclass(frozen=True)
class CommerceSafetyResult:
    category: CommerceSafetyCategory
    matched: bool
    severity: str
    requires_human: bool
    safe_response_policy: str
    matched_rules: tuple[str, ...] = ()  # rule ids only — never the customer's words
    categories: tuple[CommerceSafetyCategory, ...] = ()
    flags: tuple[str, ...] = field(default_factory=tuple)


def _any(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def classify(text: str | None, *, policy: CommercePolicy = DEFAULT_POLICY) -> CommerceSafetyResult:
    """Classify one piece of text. Pure and side-effect free; never logs or returns the text."""
    t = _normalise(text or "")
    if not t:
        return _result(C.NORMAL_BUSINESS_INQUIRY, (), (), ())
    hits: dict[CommerceSafetyCategory, list[str]] = {}
    for category, rules in _RULES.items():
        for rid, pats in rules.items():
            if _any(pats, t):
                hits.setdefault(category, []).append(rid)
    if _any(policy.extra_patterns(), t):
        hits.setdefault(C.PROHIBITED_PRODUCT, []).append("business_prohibited_term")
    flags = tuple(name for name, pats in _FLAGS.items() if _any(pats, t))
    ordered = tuple(c for c in _PRIORITY if c in hits)
    if not ordered:
        return _result(C.NORMAL_BUSINESS_INQUIRY, (), (), flags)
    return _result(ordered[0], tuple(hits[ordered[0]]), ordered, flags)


def _result(category, matched_rules, categories, flags) -> CommerceSafetyResult:
    normal = category == C.NORMAL_BUSINESS_INQUIRY
    return CommerceSafetyResult(
        category=category, matched=not normal, severity=_SEVERITY[category], requires_human=not normal,
        safe_response_policy=_RESPONSE_POLICY[category], matched_rules=matched_rules, categories=categories, flags=flags,
    )


def _rank(r: CommerceSafetyResult) -> int:
    order = {c: i for i, c in enumerate(_PRIORITY)}
    return order.get(r.category, len(_PRIORITY))


def classify_many(texts: Iterable[str | None], *, policy: CommercePolicy = DEFAULT_POLICY) -> CommerceSafetyResult:
    """The most serious classification across several fields (e.g. a lead's description and requested product)."""
    worst: CommerceSafetyResult | None = None
    flags: set[str] = set()
    for t in texts:
        r = classify(t, policy=policy)
        flags.update(r.flags)
        if worst is None or _rank(r) < _rank(worst):
            worst = r
    base = worst or classify("", policy=policy)
    if flags and set(base.flags) != flags:
        return CommerceSafetyResult(**{**base.__dict__, "flags": tuple(sorted(flags))})
    return base
