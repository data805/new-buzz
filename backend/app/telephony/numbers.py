"""Phone number normalisation for Zambia (+260) and other allowed countries."""
from __future__ import annotations

import phonenumbers

from ..config import get_settings

# Mobile prefixes (NSN) per operator as allocated by ZICTA; used for display only.
ZM_OPERATORS = {
    "95": "Zamtel", "75": "Zamtel",
    "96": "MTN", "76": "MTN",
    "97": "Airtel", "77": "Airtel",
    "98": "ZedMobile",
}


class InvalidNumber(ValueError):
    pass


def normalise(raw: str, default_region: str = "ZM") -> str:
    """Return E.164 (e.g. +260971234567) or raise InvalidNumber."""
    raw = (raw or "").strip()
    if not raw:
        raise InvalidNumber("empty number")
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    try:
        n = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException as e:
        raise InvalidNumber(f"'{raw}' is not a phone number: {e}") from e
    if not phonenumbers.is_valid_number(n):
        raise InvalidNumber(f"'{raw}' is not a valid number")
    return phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.E164)


def is_allowed_destination(e164: str) -> bool:
    cc = str(phonenumbers.parse(e164).country_code)
    return cc in get_settings().allowed_country_codes


def describe(e164: str) -> dict:
    try:
        n = phonenumbers.parse(e164)
    except phonenumbers.NumberParseException:
        return {"valid": False}
    nsn = str(n.national_number)
    kind = phonenumbers.number_type(n)
    return {
        "valid": phonenumbers.is_valid_number(n),
        "country_code": n.country_code,
        "national": phonenumbers.format_number(n, phonenumbers.PhoneNumberFormat.NATIONAL),
        "type": {phonenumbers.PhoneNumberType.MOBILE: "mobile", phonenumbers.PhoneNumberType.FIXED_LINE: "fixed"}.get(kind, "other"),
        "operator": ZM_OPERATORS.get(nsn[:2], "") if n.country_code == 260 else "",
    }


def mask(e164: str) -> str:
    return e164[:-7] + "****" + e164[-3:] if len(e164) > 8 else "****"
