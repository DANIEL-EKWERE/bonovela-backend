"""Record which IP addresses accounts use."""

from django.conf import settings
from django.core.cache import cache
from django.db.models import F


def client_ip(request):
    """The caller's IP. X-Forwarded-For is only trusted when the app runs
    behind a proxy that sets it (TRUST_X_FORWARDED_FOR=True)."""
    if getattr(settings, 'TRUST_X_FORWARDED_FOR', False):
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
        if forwarded:
            return forwarded.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR') or None


def record(user, request, event):
    from .models import IPLog

    ip = client_ip(request)
    if not ip or not user or not user.is_authenticated:
        return None
    agent = request.META.get('HTTP_USER_AGENT', '')[:255]
    log, created = IPLog.objects.get_or_create(
        user=user, ip=ip, defaults={'first_event': event, 'last_event': event, 'user_agent': agent})
    if not created:
        IPLog.objects.filter(pk=log.pk).update(hits=F('hits') + 1, last_event=event, user_agent=agent or log.user_agent)
    return ip


class IPTrackingMiddleware:
    """Logs signed-in dashboard visits, at most once an hour per account and IP."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            ip = client_ip(request)
            key = f'iplog:{user.pk}:{ip}'
            if ip and not cache.get(key):
                record(user, request, 'visit')
                cache.set(key, 1, 3600)
        return response
