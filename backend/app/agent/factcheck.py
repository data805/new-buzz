"""Deterministic guard against fabricated specifics.

Small language models sometimes invent prices, numbers or contact details even
when told not to. Before a sentence is spoken we check that every concrete
specific in it (currency amounts, numbers, phone numbers, emails, URLs) is
supported by the retrieved knowledge or by what the caller said. Unsupported
sentences are replaced with an honest fallback."""
from __future__ import annotations

import re
from dataclasses import dataclass

_CURRENCY = re.compile(r"\b(kwacha|zmw|usd|dollars?|\$|k\s?\d|ngwee|price[sd]?|costs?|fees?|charges?)\b|\$\s?\d", re.I)
_NUMBER_WORDS = re.compile(r"\b(hundred|thousand|million)\b", re.I)
_DIGITS = re.compile(r"\d[\d,.]*\d|\d")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_EMAIL_CLAIM = re.compile(r"\[email[^\]]*\]|e-?mail (address )?(is|at)\b", re.I)
# claims that something is NOT offered are policy statements too; they need a source
_NEGATIVE_CLAIM = re.compile(r"\b(not (available|offered|provided)|(don'?t|do not|doesn'?t|does not) (offer|provide)|no discounts?)\b", re.I)
_URL = re.compile(r"\b(?:https?://|www\.)\S+|\b[\w-]+\.(?:co\.zm|com|org|zm|net)\b", re.I)

PRICE_FALLBACK = ("I'm sorry, I don't have confirmed pricing for that. I can take your name and number "
                  "so the team can share the current details with you.")
GENERIC_FALLBACK = ("I'm sorry, I don't have confirmed details on that. I can take your name and number "
                    "so a member of the team can follow up with you.")

# numbers that are fine in ordinary speech without a source
_HARMLESS = {"0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "24", "7", "100"}


def _norm_num(s: str) -> str:
    return s.replace(",", "").rstrip(".")


@dataclass
class FactCheck:
    ok: bool
    reason: str = ""
    fallback: str = ""


def check_sentence(sentence: str, context: str) -> FactCheck:
    ctx = context.lower()
    ctx_nums = {_norm_num(n) for n in _DIGITS.findall(context)}
    ctx_digits_joined = re.sub(r"\D", "", context)
    money = bool(_CURRENCY.search(sentence))

    if _EMAIL_CLAIM.search(sentence) and not _EMAIL.search(context):
        return FactCheck(False, "email claimed but none in knowledge", GENERIC_FALLBACK)
    for email in _EMAIL.findall(sentence):
        if email.lower() not in ctx:
            return FactCheck(False, f"unsupported email {email}", GENERIC_FALLBACK)
    for url in _URL.findall(sentence):
        u = url.lower().removeprefix("https://").removeprefix("http://").removeprefix("www.").rstrip(".,")
        if u.split("/")[0] not in ctx:
            return FactCheck(False, f"unsupported url {url}", GENERIC_FALLBACK)

    for raw in _DIGITS.findall(sentence):
        n = _norm_num(raw)
        digits = re.sub(r"\D", "", n)
        if len(digits) >= 7:  # phone-number-like: must appear (digits only) in the context
            if digits[-9:] not in ctx_digits_joined:
                return FactCheck(False, f"unsupported number {raw}", GENERIC_FALLBACK)
            continue
        if n in ctx_nums:
            continue
        if money:
            return FactCheck(False, f"unsupported amount {raw}", PRICE_FALLBACK)
        if n not in _HARMLESS:
            return FactCheck(False, f"unsupported number {raw}", GENERIC_FALLBACK)

    m = _NEGATIVE_CLAIM.search(sentence)
    if m and m.group(0).lower() not in ctx:
        return FactCheck(False, f"unsupported negative claim '{m.group(0)}'", GENERIC_FALLBACK)
    if money and _NUMBER_WORDS.search(sentence) and not _NUMBER_WORDS.search(context):
        return FactCheck(False, "amount in words", PRICE_FALLBACK)
    return FactCheck(True)
