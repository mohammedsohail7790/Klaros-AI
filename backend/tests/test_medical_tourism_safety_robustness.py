"""Robustness of the Medical Tourism safety classifier: other languages, adversarial phrasing, conservative fallback for text it cannot read, and
documented false positives. SYNTHETIC phrases only. This is NOT clinical validation: the classifier is a deterministic lexical screen whose job is to
send anything doubtful to a person; every rule can still miss phrasing it has not seen (see the 'known limits' tests)."""
from __future__ import annotations

import pytest

from app.services.medical_tourism_safety import SafetyCategory as C
from app.services.medical_tourism_safety import classify, classify_many

EMERGENCY_PHRASES = [
    ("en", "I have chest pain and cannot breathe"),
    ("ar", "عندي ألم في الصدر وأحتاج مساعدة"),
    ("ar", "لا أستطيع التنفس"),
    ("ar", "أريد أن أنهي حياتي"),
    ("tr", "Göğüs ağrım var ve nefes alamıyorum"),
    ("tr", "Kalp krizi geçiriyorum, acil"),
    ("ru", "У меня сильная боль в груди, не могу дышать"),
    ("ru", "Он потерял сознание, нужна скорая"),
    ("es", "Tengo dolor de pecho y no puedo respirar"),
    ("es", "Quiero morir, necesito una ambulancia"),
    ("fr", "J'ai une douleur thoracique, je ne peux pas respirer"),
    ("fr", "C'est une urgence, crise cardiaque"),
    ("de", "Ich habe starke Brustschmerzen und Atemnot"),
    ("de", "Das ist ein Notfall, bitte Krankenwagen"),
]


@pytest.mark.parametrize("lang,text", EMERGENCY_PHRASES)
def test_emergency_language_in_six_languages_is_an_emergency(lang, text) -> None:
    r = classify(text)
    assert r.category == C.EMERGENCY and r.requires_human and r.severity == "critical", (lang, r.category)


DIAGNOSIS = ["Do I have cancer?", "هل أعاني من مرض في القلب", "Kanser mi acaba", "У меня рак?", "¿Tengo cáncer?", "Ai-je un cancer ?", "Habe ich Krebs?"]
PRESCRIPTION = ["Which medication should I take?", "أريد وصفة طبية", "Hangi ilaç ve doz", "Какое лекарство мне пить", "¿Qué medicamento y qué dosis?", "Quel médicament, quelle posologie", "Welches Medikament, welche Dosierung"]
GUARANTEE = ["Can you guarantee the result?", "هل يوجد ضمان للنتيجة", "Sonucu garanti ediyor musunuz", "Вы гарантируете результат?", "¿Me garantizan el resultado?", "Garantissez-vous le résultat, sans risque ?", "Können Sie den Erfolg garantieren, ohne Risiko?"]


@pytest.mark.parametrize("text", DIAGNOSIS)
def test_diagnosis_requests_in_other_languages_go_to_a_person(text) -> None:
    assert classify(text).category in (C.DIAGNOSIS_REQUEST, C.EMERGENCY)


@pytest.mark.parametrize("text", PRESCRIPTION)
def test_prescription_requests_in_other_languages_go_to_a_person(text) -> None:
    assert classify(text).category in (C.PRESCRIPTION_REQUEST, C.EMERGENCY)


@pytest.mark.parametrize("text", GUARANTEE)
def test_outcome_guarantee_requests_in_other_languages_go_to_a_person(text) -> None:
    assert classify(text).category in (C.OUTCOME_GUARANTEE_REQUEST, C.EMERGENCY)


# ----------------------------------------------------------------------------------------------- adversarial phrasing
OBFUSCATED = [
    "c h e s t  p a i n",
    "c.h.e.s.t p-a-i-n",
    "ch3st p@in",
    "I can't br e a the",
    "сhest pain",                      # Cyrillic 'с'
    "ch​est​ pain",           # zero-width spaces
    "ch­est pain",                 # soft hyphen
    "he took an over-dose",
    "I want to k1ll myself",
    "my chest   pain is bad",
    "CHEST PAIN!!!",
    "ＣＨＥＳＴ ＰＡＩＮ",                   # full-width letters
]


@pytest.mark.parametrize("text", OBFUSCATED)
def test_obfuscated_emergency_wording_is_still_caught(text) -> None:
    assert classify(text).category == C.EMERGENCY


# ----------------------------------------------------------------------------------------------- benign text must stay benign
BENIGN = [
    "I would like a consultation about dental implants next spring",
    "Looking for rhinoplasty in Istanbul, budget 4000 euros, travel in May",
    "Hi, I am interested in a hair transplant for my husband, please send prices",
    "Can you send me the price list and the available dates for next month please",
    "Hello",
    "Dental implants",
    "Istanbul",
    "Ahmet Yilmaz",
    "knee replacement",
]


@pytest.mark.parametrize("text", BENIGN)
def test_ordinary_business_inquiries_are_not_flagged(text) -> None:
    r = classify(text)
    assert r.category == C.NORMAL_BUSINESS_INQUIRY and not r.requires_human, (text, r.category)


# ----------------------------------------------------------------------------------------------- conservative fallback for unreadable text
@pytest.mark.parametrize("text", [
    "أريد زراعة الأسنان في تركيا في الصيف القادم",                                                   # Arabic prose, no safety term
    "Здравствуйте, хочу узнать о стоимости имплантации зубов в вашей клинике",                      # Russian prose
    "你好，我想了解牙齿种植的价格",                                                                     # Chinese
    "मुझे दांतों के इम्प्लांट के बारे में जानकारी चाहिए",                                              # Hindi
    "Merhaba, diş implantı fiyatları ve süreç hakkında bilgi almak istiyorum lütfen yardımcı olur musunuz",   # Turkish prose
    "Ich möchte eine Beratung zu Zahnimplantaten im nächsten Frühling bitte und wie teuer es ist",   # German prose
])
def test_text_the_classifier_cannot_read_goes_to_a_person_instead_of_passing(text) -> None:
    r = classify(text)
    assert r.category == C.LANGUAGE_UNSUPPORTED and r.requires_human and r.severity == "medium"
    assert "person" in r.safe_response_policy


def test_a_specific_safety_category_wins_over_the_unreadable_text_fallback() -> None:
    r = classify("أريد زراعة الأسنان لكن عندي ألم في الصدر")
    assert r.category == C.EMERGENCY
    assert C.LANGUAGE_UNSUPPORTED in r.categories                          # both are recorded; the higher priority leads


def test_classify_many_keeps_the_most_serious_category_across_fields() -> None:
    r = classify_many(["Dental implants", "ألم في الصدر", None])
    assert r.category == C.EMERGENCY


def test_the_result_never_contains_the_patients_words() -> None:
    text = "ألم في الصدر — my name is Zelda Qwertyson, mail zelda@example.com"
    blob = repr(classify(text)) + repr(classify_many([text]))
    for needle in ("Zelda", "Qwertyson", "zelda@example.com", "الصدر"):
        assert needle not in blob


# ----------------------------------------------------------------------------------------------- known limits, pinned so a change is noticed
@pytest.mark.parametrize("text", [
    "I do not have chest pain, I only want a quote",     # negation is not understood: flagged (a person reads it)
    "swimming back stroke classes",                      # 'stroke' as a word: flagged
    "Is parking available at the clinic?",               # 'the clinic' reads as an unverified provider name: flagged
])
def test_known_false_positives_err_towards_a_person_never_towards_passing(text) -> None:
    assert classify(text).requires_human


def test_the_classifier_is_lexical_and_does_not_understand_meaning() -> None:
    """A paraphrase with none of the listed words in a language it can read passes as normal. The mitigation is human review of every lead before any
    clinical statement is made; this is why the screen is documented as a safety net, not a guarantee."""
    assert classify("I feel really off and weak since yesterday and my family is worried").category == C.NORMAL_BUSINESS_INQUIRY
    # the listed ambiguous symptoms ARE sent to a person (severity high, not the emergency script)
    for text in ("my heart is racing and I feel dizzy", "I have numbness in my arm", "I keep vomiting after the operation", "I am coughing up blood"):
        r = classify(text)
        assert r.category == C.EMERGENCY and r.requires_human, text
