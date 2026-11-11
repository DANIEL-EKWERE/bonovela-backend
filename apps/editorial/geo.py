"""Countries, their states/regions (ISO 3166) and dialing codes, for the
contract application on the web and in the app."""

from functools import lru_cache

import phonenumbers
import pycountry


@lru_cache
def countries():
    """[{code, name, dial}] sorted by name; dial is like '+234'."""
    out = []
    for c in pycountry.countries:
        code = phonenumbers.country_code_for_region(c.alpha_2)
        out.append({'code': c.alpha_2, 'name': getattr(c, 'common_name', c.name), 'dial': f'+{code}' if code else ''})
    return sorted(out, key=lambda c: c['name'])


@lru_cache
def states(country_code: str):
    """Top-level subdivisions of a country: [{code, name}] sorted by name."""
    subs = pycountry.subdivisions.get(country_code=(country_code or '').upper()) or []
    return sorted(({'code': s.code, 'name': s.name} for s in subs if not s.parent_code), key=lambda s: s['name'])


def country_name(code: str):
    return next((c['name'] for c in countries() if c['code'] == code), code)


def state_name(code: str):
    s = pycountry.subdivisions.get(code=code) if code else None
    return s.name if s else code


def normalise_phone(dial: str, number: str):
    """'+234', '0801 234 5678' → '+2348012345678', or raise ValueError."""
    raw = f"{dial}{(number or '').lstrip('0')}" if dial and not (number or '').startswith('+') else number
    try:
        parsed = phonenumbers.parse(raw or '', None)
    except phonenumbers.NumberParseException as e:
        raise ValueError('Enter a valid phone number.') from e
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError('Enter a valid phone number.')
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
