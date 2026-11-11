"""Navigation for the three web areas. Each item: (url name, icon, label)."""

from django.urls import NoReverseMatch, reverse

AUTHOR_NAV = [
    ('dashboard:author_stories', 'books', 'Stories'),
    ('dashboard:author_discover', 'gift', 'Benefits'),
    ('dashboard:author_calendar', 'calendar-blank', 'Calendar'),
    ('dashboard:author_comments', 'chat-circle-dots', 'Comments'),
    ('dashboard:author_income', 'wallet', 'Income'),
]

# Editorial nav, by level. 4th item: extras (badge, hint, short label, levels).
EDITORIAL_NAV = [
    ('dashboard:editorial_home', 'squares-four', 'Overview', {}),
    ('dashboard:editorial_team', 'users-four', 'Senior Editors', {'levels': 'ce', 'short': 'Team'}),
    ('dashboard:editorial_authors', 'users', 'Authors & Accounts', {'short': 'Authors'}),
    ('dashboard:editorial_books', 'book-open', 'Book Management', {'short': 'Books'}),
    ('dashboard:editorial_queue', 'clock-counter-clockwise', 'Submission History', {'short': 'Submissions'}),
    ('dashboard:editorial_completion', 'magnifying-glass', 'AI Completion Review',
     {'badge': 'New', 'hint': 'Full manuscript analysis and completion applications'}),
    ('dashboard:editorial_plagiarism', 'shield-check', 'Plagiarism Checker', {}),
    ('dashboard:editorial_duplicates', 'copy', 'Duplicate Chapter Detector', {}),
    ('dashboard:editorial_performance', 'chart-line-up', 'Team Performance', {'levels': 'ce'}),
    ('dashboard:editorial_recruitment', 'user-plus', 'Recruitment', {}),
    ('dashboard:editorial_shelves', 'books', 'App Shelves', {'levels': 'se ce', 'hint': 'Curated shelves in the app and Short Stories', 'short': 'Shelves'}),
    ('dashboard:editorial_promotions', 'megaphone', 'Promotions', {'levels': 'se ce'}),
    ('dashboard:editorial_commission', 'coins', 'Commission', {}),
    ('dashboard:editorial_escalations', 'warning-octagon', 'Escalations', {}),
    ('dashboard:editorial_messages', 'chat-centered-text', 'Messages', {}),
    ('dashboard:editorial_reports', 'clipboard-text', 'Reports', {}),
]

DRAMA_NAV = [
    ('dashboard:drama_list', 'film-slate', 'Dramas'),
    ('dashboard:drama_stats_all', 'chart-bar', 'Analytics'),
    ('dashboard:drama_earnings', 'wallet', 'Earnings'),
]

MARKETING_NAV = [
    ('dashboard:marketing_home', 'chart-line-up', 'Overview'),
    ('dashboard:marketing_banners', 'image', 'Banners'),
    ('dashboard:marketing_codes', 'ticket', 'Promo codes'),
    ('dashboard:marketing_broadcasts', 'megaphone', 'Broadcasts'),
]

FINANCE_NAV = [
    ('dashboard:finance_home', 'chart-line-up', 'Overview'),
    ('dashboard:finance_transactions', 'receipt', 'Transactions'),
    ('dashboard:finance_payouts', 'hand-coins', 'Payouts'),
    ('dashboard:finance_balances', 'users-three', 'Balances'),
    ('dashboard:finance_statements', 'file-text', 'Statements'),
]

ADMIN_NAV = [
    ('dashboard:admin_home', 'chart-line-up', 'Overview'),
    ('dashboard:admin_users', 'users', 'Users'),
    ('dashboard:admin_books', 'books', 'Books'),
    ('dashboard:admin_money', 'coins', 'Revenue'),
    ('dashboard:admin_reports', 'flag', 'Reports'),
    ('dashboard:admin_store', 'storefront', 'Store'),
]


def _items(nav, path, lv=None):
    out = []
    for name, icon, label, *extra in nav:
        extra = extra[0] if extra else {}
        if lv and 'levels' in extra and lv not in extra['levels'].split():
            continue
        try:
            url = reverse(name)
        except NoReverseMatch:
            continue
        home = url.rstrip('/').count('/') <= 1
        active = path == url if home else path.startswith(url)
        out.append({'url': url, 'icon': icon, 'label': label, 'active': active, **extra})
    # Area roots are only active on an exact match; fall back to the first item.
    if out and not any(i['active'] for i in out):
        out[0]['active'] = path.rstrip('/') == out[0]['url'].rstrip('/')
    return out


def dashboard_globals(request):
    path = request.path
    user = getattr(request, 'user', None)
    area, nav, lv = None, [], None
    if path.startswith('/author'):
        area, nav = 'Author Center', AUTHOR_NAV
    elif path.startswith('/editorial'):
        from apps.editorial.team import level
        if user and user.is_authenticated:
            lv = level(user)
        area = {'ce': 'Chief Editor', 'se': 'Senior Editor'}.get(lv, 'Editorial')
        nav = EDITORIAL_NAV
    elif path.startswith('/drama-studio'):
        area, nav = 'Drama Studio', DRAMA_NAV
    elif path.startswith('/marketing'):
        area, nav = 'Marketing', MARKETING_NAV
    elif path.startswith('/finance'):
        area, nav = 'Finance', FINANCE_NAV
    elif path.startswith('/admin-panel'):
        area, nav = 'Admin', ADMIN_NAV

    # Areas this user may switch to, shown in the sidebar.
    areas = []
    if user and user.is_authenticated:
        admin = user.is_platform_admin
        if user.role in (user.ROLE_READER, user.ROLE_AUTHOR) or user.is_editor or admin:
            areas.append(('Author Center', reverse('dashboard:author_stories')))
        if user.is_editor or admin:
            from apps.editorial.team import level
            areas.append(({'ce': 'Chief Editor', 'se': 'Senior Editor'}.get(level(user), 'Senior Editor'),
                          reverse('dashboard:editorial_home')))
        if user.is_drama_creator or admin:
            areas.append(('Drama Studio', reverse('dashboard:drama_list')))
        if user.is_marketing or admin:
            areas.append(('Marketing', reverse('dashboard:marketing_home')))
        if user.is_finance or admin:
            areas.append(('Finance', reverse('dashboard:finance_home')))
        if admin:
            areas.append(('Admin', reverse('dashboard:admin_home')))

    return {
        'brand_name': 'Bonovela',
        'area': area,
        'nav': _items(nav, path, lv),
        'areas': areas,
    }
