from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied


def role_area(*roles):
    """Signed-in users with one of these roles (platform admins always pass)."""

    def decorator(view):
        @login_required
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not (user.role in roles or user.is_platform_admin):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return wrapper

    return decorator
