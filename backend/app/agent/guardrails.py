"""Deterministic safety nets that do not depend on the LLM getting it right."""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from ..services.settings import OperatingHours

_OPT_OUT = re.compile(
    r"\b(stop calling|don'?t call (me|us|this number)|do not call|never call|remove (me|my number)|"
    r"take me off|unsubscribe|opt(ed)? out|no more calls|stop contacting)\b", re.I)
_GOODBYE = re.compile(r"^\W*(ok(ay)?[, ]*)?(thank(s| you)[\w ,]*)?\W*(bye|goodbye|bye[- ]bye|that'?s all|nothing else|no,? that'?s (it|all))\W*$", re.I)
_VOICEMAIL = re.compile(
    r"(leave (a|your) message|after the (tone|beep)|is not available|cannot be reached|can'?t take your call|"
    r"voice ?mail|mailbox|the (number|subscriber) you have (dialled|dialed|called)|please try again later|"
    r"record your message)", re.I)
_DONT_KNOW = re.compile(
    r"(don'?t have (that|this|any|specific|the) (information|details)|not sure about that|i don'?t know|"
    r"unable to (find|confirm)|no information (on|about))", re.I)

CONTROL_TAGS = ("[TRANSFER]", "[END_CALL]")


def wants_human(text: str, keywords: list[str]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keywords)


def wants_opt_out(text: str) -> bool:
    return bool(_OPT_OUT.search(text))


_FAREWELL_END = re.compile(r"\b(bye|goodbye|bye[- ]bye|that'?s all|that is all|nothing else|that'?s it)$")


def is_goodbye(text: str) -> bool:
    """A short closing utterance ending in a farewell, e.g. "No, that's all. Thank you, bye."."""
    if "?" in text:
        return False
    norm = re.sub(r"[^\w' -]", " ", text.lower())
    norm = re.sub(r"\s+", " ", norm).strip()
    return len(norm.split()) <= 10 and bool(_FAREWELL_END.search(norm) or _GOODBYE.match(text.strip()))


def looks_like_voicemail(text: str) -> bool:
    return bool(_VOICEMAIL.search(text))


def admits_unknown(text: str) -> bool:
    return bool(_DONT_KNOW.search(text))


def within_hours(hours: OperatingHours, now: datetime | None = None) -> bool:
    tz = ZoneInfo(hours.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    if now.weekday() not in hours.days:
        return False
    hhmm = now.strftime("%H:%M")
    return hours.open <= hhmm < hours.close


class TagFilter:
    """Removes [TRANSFER]/[END_CALL] control tags from a token stream, even when
    a tag is split across several tokens, and records which tags were seen."""

    def __init__(self) -> None:
        self.buf = ""
        self.tags: set[str] = set()

    def feed(self, token: str) -> str:
        self.buf += token
        out = ""
        while self.buf:
            i = self.buf.find("[")
            if i < 0:
                out += self.buf
                self.buf = ""
                break
            out += self.buf[:i]
            self.buf = self.buf[i:]
            j = self.buf.find("]")
            if j < 0:
                if len(self.buf) > 14:  # not a control tag
                    out += self.buf[0]
                    self.buf = self.buf[1:]
                    continue
                break
            tag = self.buf[: j + 1]
            if tag.upper().replace(" ", "_") in CONTROL_TAGS:
                self.tags.add(tag.upper().replace(" ", "_"))
            else:
                out += tag
            self.buf = self.buf[j + 1:]
        return out

    def flush(self) -> str:
        rest = self.buf if not any(self.buf.upper().startswith(t[:len(self.buf)]) for t in CONTROL_TAGS) else ""
        self.buf = ""
        return rest
