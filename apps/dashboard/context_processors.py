"""Navigation for the three web areas. Each item: (url name, icon, label)."""

from django.urls import NoReverseMatch, reverse

AUTHOR_NAV = [
    ('dashboard:author_stories', 'books', 'Stories'),
    ('dashboard:author_discover', 'compass', 'Discover'),
    ('dashboard:author_calendar', 'calendar-blank', 'Calendar'),
    ('dashboard:author_comments', 'chat-circle-dots', 'Comments'),
    ('dashboard:author_income', 'wallet', 'Income'),
]

EDITORIAL_NAV = [
    ('dashboard:editorial_home', 'squares-four', 'Overview'),
    ('dashboard:editorial_queue', 'clipboard-text', 'Review queue'),
    ('dashboard:editorial_promotions', 'megaphone', 'Promotions'),
    ('dashboard:editorial_books', 'books', 'Books'),
]

ADMIN_NAV = [
    ('dashboard:admin_home', 'chart-line-up', 'Overview'),
    ('dashboard:admin_users', 'users', 'Users'),
    ('dashboard:admin_books', 'books', 'Books'),
    ('dashboard:admin_money', 'coins', 'Revenue'),
    ('dashboard:admin_reports', 'flag', 'Reports'),
    ('dashboard:admin_store', 'storefront', 'Store'),
]


def _items(nav, path):
    out = []
    for name, icon, label in nav:
        try:
            url = reverse(name)
        except NoReverseMatch:
            continue
        home = url.rstrip('/').count('/') <= 1
        active = path == url if home else path.startswith(url)
        out.append({'url': url, 'icon': icon, 'label': label, 'active': active})
    # Area roots are only active on an exact match; fall back to the first item.
    if out and not any(i['active'] for i in out):
        out[0]['active'] = path.rstrip('/') == out[0]['url'].rstrip('/')
    return out


def dashboard_globals(request):
    path = request.path
    user = getattr(request, 'user', None)
    area, nav = None, []
    if path.startswith('/author'):
        area, nav = 'Author Center', AUTHOR_NAV
    elif path.startswith('/editorial'):
        area, nav = 'Editorial', EDITORIAL_NAV
    elif path.startswith('/admin-panel'):
        area, nav = 'Admin', ADMIN_NAV

    # Areas this user may switch to, shown in the sidebar.
    areas = []
    if user and user.is_authenticated:
        candidates = [('Author Center', 'dashboard:author_stories', True),
                      ('Editorial', 'dashboard:editorial_home', user.is_editor or user.is_platform_admin),
                      ('Admin', 'dashboard:admin_home', user.is_platform_admin)]
        for name, url_name, allowed in candidates:
            if not allowed:
                continue
            try:
                areas.append((name, reverse(url_name)))
            except NoReverseMatch:  # area not routed yet
                pass

    return {
        'brand_name': 'Bonovela',
        'area': area,
        'nav': _items(nav, path),
        'areas': areas,
    }
