"""Fast unit tests: no models, no network."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.agent import guardrails
from app.agent.summarizer import _sanitise
from app.knowledge.store import chunk_text, tokenize
from app.services.settings import OperatingHours
from app.telephony.numbers import InvalidNumber, describe, normalise
from app.voice.tts import SentenceChunker, normalise_for_speech
from app.workflows.actions import render
from app.workflows.engine import condition_matches, idempotency_key


# ---------------------------------------------------------------- phone numbers
@pytest.mark.parametrize("raw,expected", [
    ("0971234567", "+260971234567"),
    ("+260 97 123 4567", "+260971234567"),
    ("00260961234567", "+260961234567"),
    ("0211845600", "+260211845600"),
    ("+27 82 123 4567", "+27821234567"),
])
def test_normalise_numbers(raw, expected):
    assert normalise(raw) == expected


@pytest.mark.parametrize("raw", ["", "12345", "hello", "+260 12"])
def test_invalid_numbers(raw):
    with pytest.raises(InvalidNumber):
        normalise(raw)


def test_zambian_operator_lookup():
    assert describe("+260971234567")["operator"] == "Airtel"
    assert describe("+260961234567")["operator"] == "MTN"
    assert describe("+260951234567")["operator"] == "Zamtel"
    assert describe("+260211845600")["type"] == "fixed"


# ---------------------------------------------------------------- guardrails
@pytest.mark.parametrize("text", ["Please stop calling me", "don't call this number again", "remove my number from your list", "I want to opt out"])
def test_opt_out_detected(text):
    assert guardrails.wants_opt_out(text)


@pytest.mark.parametrize("text", ["Can you call me back tomorrow?", "I stopped by your office", "What time do you open?"])
def test_opt_out_not_false_positive(text):
    assert not guardrails.wants_opt_out(text)


def test_human_request():
    kw = ["speak to a human", "real person"]
    assert guardrails.wants_human("Can I speak to a human please", kw)
    assert not guardrails.wants_human("What is the AI lab", kw)


@pytest.mark.parametrize("text,expected", [("No, that's all. Thank you, bye.", True), ("Goodbye", True), ("Bye bye", True), ("Bye the way, how much is a desk?", False)])
def test_goodbye(text, expected):
    assert guardrails.is_goodbye(text) is expected


def test_voicemail_detection():
    assert guardrails.looks_like_voicemail("The subscriber you have called is not available. Please leave a message after the tone.")
    assert not guardrails.looks_like_voicemail("Hello? Who is this?")


def test_within_hours():
    h = OperatingHours(days=[0, 1, 2, 3, 4], open="08:00", close="17:00")
    tz = ZoneInfo("Africa/Lusaka")
    assert guardrails.within_hours(h, datetime(2026, 10, 27, 10, 0, tzinfo=tz))  # Tuesday
    assert not guardrails.within_hours(h, datetime(2026, 10, 27, 18, 0, tzinfo=tz))
    assert not guardrails.within_hours(h, datetime(2026, 10, 25, 10, 0, tzinfo=tz))  # Sunday


def test_tag_filter_strips_split_tags():
    f = guardrails.TagFilter()
    out = "".join(f.feed(t) for t in ["Goodbye, have a ", "great day. [EN", "D_CA", "LL]"]) + f.flush()
    assert out.strip() == "Goodbye, have a great day."
    assert "[END_CALL]" in f.tags


def test_tag_filter_keeps_normal_brackets():
    f = guardrails.TagFilter()
    out = f.feed("Price [see website] is listed") + f.flush()
    assert out == "Price [see website] is listed"
    assert not f.tags


# ---------------------------------------------------------------- text / speech
def test_sentence_chunker_streams_early():
    c = SentenceChunker()
    out = []
    for tok in "Sure, I can help with that. Our coworking space is at ZEP-RE Business Park. What else?".split(" "):
        out += c.feed(tok + " ")
    out += c.flush()
    assert out[0].startswith("Sure")
    assert "".join(out).replace(" ", "") == "Sure,Icanhelpwiththat.OurcoworkingspaceisatZEP-REBusinessPark.Whatelse?"
    assert len(out) >= 3


def test_speech_normalisation():
    t = normalise_for_speech("Printing costs K3 at BongoHive, see https://x.y **now**")
    assert "3 kwacha" in t and "Bongo Hive" in t and "http" not in t and "*" not in t


# ---------------------------------------------------------------- knowledge
def test_chunking_keeps_headings():
    text = "# Doc\n\n## Where are you?\nLusaka.\n\n## Hours\n" + ("Open daily. " * 150)
    chunks = chunk_text(text, target_chars=500)
    assert chunks[0].heading == "Where are you?"
    assert all(c.heading == "Hours" for c in chunks[1:])
    assert all(len(c.text) <= 700 for c in chunks)


def test_tokenize_drops_stopwords():
    assert tokenize("What is the price of a hot desk?") == ["price", "hot", "desk"]


# ---------------------------------------------------------------- summariser
def test_sanitise_drops_hallucinated_contact_details():
    transcript = "Caller: Hi, my name is Mwila, call me on 0971234567\nAgent: Thanks Mwila."
    out = _sanitise({"caller_name": "John Phiri", "caller_phone": "0961111111", "caller_email": "a@b.com",
                     "organisation": "BongoHive", "intent": "nonsense", "feedback_rating": 9}, transcript)
    assert out["caller_name"] is None
    assert out["caller_phone"] == "0971234567"  # recovered from transcript instead
    assert out["caller_email"] is None
    assert out["organisation"] is None
    assert out["intent"] == "other"
    assert out["feedback_rating"] is None


# ---------------------------------------------------------------- workflows
def test_conditions():
    p = {"extracted": {"intent": "coworking", "rating": 2, "callback_requested": True, "topic": None}}
    assert condition_matches({"field": "extracted.intent", "op": "eq", "value": "Coworking"}, p)
    assert condition_matches({"field": "extracted.rating", "op": "lt", "value": 3}, p)
    assert condition_matches({"field": "extracted.callback_requested", "op": "truthy"}, p)
    assert condition_matches({"field": "extracted.topic", "op": "not_exists"}, p)
    assert condition_matches({"field": "extracted.intent", "op": "in", "value": "ai_lab, coworking"}, p)
    assert not condition_matches({"field": "extracted.rating", "op": "gt", "value": "abc"}, p)


def test_template_rendering_with_fallback():
    p = {"phone": "+260211000000", "extracted": {"caller_phone": "", "caller_name": "Mwila"}}
    assert render("{{extracted.caller_phone||phone}}", p) == "+260211000000"
    assert render("Hi {{extracted.caller_name}}!", p) == "Hi Mwila!"
    assert render({"a": ["{{missing}}"]}, p) == {"a": [""]}


def test_idempotency_key_stable():
    assert idempotency_key("w", "call.completed", {"call_id": "c1"}) == "w:call.completed:c1"
    assert idempotency_key("w", "custom", {"x": 1}) == idempotency_key("w", "custom", {"x": 1})


# ---------------------------------------------------------------- audio
def test_streaming_vad_detects_speech_vs_silence():
    from app.voice.vad import StreamingVAD

    vad = StreamingVAD()
    silence = np.zeros(16000, dtype=np.float32)
    assert max(p for _, p in vad.process(silence)) < 0.3


# ---------------------------------------------------------------- fact guard
from app.agent.factcheck import PRICE_FALLBACK, check_sentence  # noqa: E402

_CTX = """KNOWLEDGE: Hot Desk access Monday to Friday, 8am to 5pm. Printing at 3 kwacha per page.
Prices are not published. Up to 50,000 US dollars in follow-on funding. Phone +260 211 845 600.
Contact form at bongohive.co.zm/get-in-touch. Applications closed on 24 July 2026."""


@pytest.mark.parametrize("sentence", [
    "Hot desk access is Monday to Friday, 8am to 5pm.",
    "Printing costs 3 kwacha per page.",
    "You could receive up to 50,000 US dollars in follow-on funding.",
    "You can call us on +260 211 845 600.",
    "Please use the contact form at bongohive.co.zm/get-in-touch.",
    "Applications closed on 24 July 2026.",
    "Prices are not published, but I can take your details.",
])
def test_fact_guard_allows_supported(sentence):
    assert check_sentence(sentence, _CTX).ok


@pytest.mark.parametrize("sentence,price", [
    ("The Hot Desk plan costs 300 kwacha per month.", True),
    ("A private office is K2,500 a month.", True),
    ("It costs about five hundred kwacha.", True),
    ("Call our office on 0977 123 456.", False),
    ("The email address for the AI Lab is [email protected].", False),
    ("Email us at ailab@bongohive.co.zm.", False),
    ("We have helped 450 startups.", False),
    ("Visit www.bongohive-ai.com for details.", False),
    ("Discounts are not available for students at this time.", False),
    ("We don't offer evening sessions.", False),
])
def test_fact_guard_blocks_fabrications(sentence, price):
    fc = check_sentence(sentence, _CTX)
    assert not fc.ok
    assert (fc.fallback == PRICE_FALLBACK) is price
