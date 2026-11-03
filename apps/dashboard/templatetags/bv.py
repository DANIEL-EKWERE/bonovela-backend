from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def money(cents):
    """2100 → $21.00"""
    cents = int(cents or 0)
    sign = '-' if cents < 0 else ''
    cents = abs(cents)
    return f'{sign}${cents // 100:,}.{cents % 100:02d}'


@register.filter
def compact(n):
    n = int(n or 0)
    if n >= 1_000_000:
        return f'{n / 1_000_000:.1f}'.rstrip('0').rstrip('.') + 'M'
    if n >= 1000:
        return f'{n / 1000:.1f}'.rstrip('0').rstrip('.') + 'K'
    return str(n)


@register.filter
def stars(n):
    n = int(n or 0)
    return mark_safe('<span class="stars">' + '★' * n + '<span style="opacity:.25">' + '★' * (5 - n) + '</span></span>')


@register.inclusion_tag('dashboard/partials/cover.html')
def cover(story, width=84, title=None):
    return {'story': story, 'width': width, 'title': title if title is not None else story.display_title}


@register.filter
def get_item(d, key):
    return d.get(key) if hasattr(d, 'get') else None
