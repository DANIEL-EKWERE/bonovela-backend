"""One set of IDs for readers, authors and staff, shown in the app and
searchable in every staff dashboard:

    Reader ID   RD-100132   (100000 + account number)
    Author ID   AUT-32
    Staff ID    STF-32

Searches also accept the bare account number, the bare Reader ID number
and the email."""

import re

from django.db.models import Q

READER_BASE = 100000


def reader_id(user):
    return f'RD-{READER_BASE + user.pk}'


def author_id(user):
    return f'AUT-{user.pk}'


def user_pks(q):
    """Account numbers a search text could mean (empty if none)."""
    code = re.sub(r'\s+', '', (q or '')).upper()
    m = re.fullmatch(r'(RD|AUT|STF)-?(\d+)', code)
    if m:
        n = int(m.group(2))
        return {n - READER_BASE} if m.group(1) == 'RD' else {n}
    if code.isdigit():
        n = int(code)
        return {n, n - READER_BASE} if n > READER_BASE else {n}
    return set()


def user_q(q, prefix=''):
    """A Q matching people by Reader / Author / Staff ID, account number,
    email, username or pen name. [prefix] is the path to the user, e.g.
    'author__' or 'user__'."""
    q = (q or '').strip()
    cond = (Q(**{f'{prefix}email__icontains': q}) | Q(**{f'{prefix}username__icontains': q})
            | Q(**{f'{prefix}pen_name__icontains': q}))
    pks = user_pks(q)
    if pks:
        cond |= Q(**{f'{prefix}pk__in': pks})
    return cond
