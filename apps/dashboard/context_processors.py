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

# Drama Studio is closed for now (routes commented out in urls.py).
# DRAMA_NAV = [
#     ('dashboard:drama_list', 'film-slate', 'Dramas'),
#     ('dashboard:drama_stats_all', 'chart-bar', 'Analytics'),
#     ('dashboard:drama_earnings', 'wallet', 'Earnings'),
# ]

MARKETING_NAV = [
    ('dashboard:marketing_home', 'squares-four', 'Marketing Overview', {'section': 'Overview'}),
    ('dashboard:marketing_advertising', 'chart-bar', 'Advertising Analytics'),
    ('dashboard:marketing_meta', 'meta-logo', 'Meta Ads', {'section': 'Ad platforms'}),
    ('dashboard:marketing_google', 'magnifying-glass', 'Google Ads'),
    ('dashboard:marketing_tiktok', 'tiktok-logo', 'TikTok Ads'),
    ('dashboard:marketing_apple', 'apple-logo', 'Apple Ads'),
    ('dashboard:marketing_readers', 'users', 'Reader Analytics', {'section': 'Readers & books'}),
    ('dashboard:marketing_paying', 'wallet', 'Paying Readers'),
    ('dashboard:marketing_nonpaying', 'user-minus', 'Non-Paying Readers'),
    ('dashboard:marketing_books', 'book-open', 'Book Performance'),
    ('dashboard:marketing_promotions', 'megaphone-simple', 'Promotion Centre', {'section': 'Promotions'}),
    ('dashboard:marketing_discovery', 'compass', 'Discovery Page'),
    ('dashboard:marketing_banners', 'image', 'Banner Promotions'),
    ('dashboard:marketing_popups', 'selection', 'Pop-up Promotions'),
    ('dashboard:marketing_opening', 'device-mobile', 'Opening Screen'),
    ('dashboard:marketing_notifications', 'bell', 'Notification Promotions'),
    ('dashboard:marketing_broadcasts', 'broadcast', 'Broadcast Centre', {'section': 'Engagement'}),
    ('dashboard:marketing_codes', 'ticket', 'Promo Codes'),
    ('dashboard:marketing_links', 'link', 'Deep & Campaign Links'),
    ('dashboard:marketing_funnel', 'funnel', 'Conversion Funnel', {'section': 'Insights'}),
    ('dashboard:marketing_retention', 'arrows-left-right', 'Reader Retention'),
    ('dashboard:marketing_reports', 'file-text', 'Reports & ROAS'),
    ('dashboard:marketing_settings', 'gear-six', 'Marketing Settings', {'section': 'Admin'}),
]

# Pages appear once their URL exists (finance is built in stages).
FINANCE_NAV = [
    ('dashboard:finance_home', 'squares-four', 'Finance Overview', {'section': 'Overview'}),
    ('dashboard:finance_transactions', 'arrows-left-right', 'All Transactions', {'section': 'Records'}),
    ('dashboard:finance_readers', 'book-open-text', 'Reader Records'),
    ('dashboard:finance_authors', 'crown-simple', 'Author Records'),
    ('dashboard:finance_staff', 'users', 'Staff Records'),
    ('dashboard:finance_coins', 'coins', 'Coin Purchases', {'section': 'Revenue streams'}),
    ('dashboard:finance_subscription_payments', 'arrows-clockwise', 'Subscriptions'),
    ('dashboard:finance_gifts', 'gift', 'Reader Gifts'),
    ('dashboard:finance_approvals', 'stamp', 'Payment Approval Centre', {'section': 'Payments'}),
    ('dashboard:finance_withdrawals', 'hand-coins', 'Withdrawals'),
    ('dashboard:finance_treasury', 'bank', 'Bonovela Treasury', {'section': 'Treasury'}),
    ('dashboard:finance_search', 'magnifying-glass', 'Universal Search', {'section': 'Finance Operations'}),
    ('dashboard:finance_revenue', 'chart-pie-slice', 'Revenue Distribution'),
    ('dashboard:finance_subscriptions', 'book-open', 'Short Story Subscriptions'),
    ('dashboard:finance_audit', 'scroll', 'Audit Logs'),
    ('dashboard:finance_settings', 'gear-six', 'Finance Settings'),
    ('dashboard:finance_team', 'user-gear', 'Team & Permissions'),
    ('dashboard:finance_refunds', 'receipt-x', 'Refunds & Chargebacks'),
    ('dashboard:finance_support', 'lifebuoy', 'Reader Support'),
    ('dashboard:finance_bonuses', 'sparkle', 'Bonuses & Incentives'),
    ('dashboard:finance_expenses', 'currency-circle-dollar', 'Expenses'),
    ('dashboard:finance_reports', 'file-text', 'Financial Reports'),
    ('dashboard:finance_reconciliation', 'scales', 'Reconciliation'),
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
    # elif path.startswith('/drama-studio'):
    #     area, nav = 'Drama Studio', DRAMA_NAV
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
        # Drama Studio is closed for now.
        # if user.is_drama_creator or admin:
        #     areas.append(('Drama Studio', reverse('dashboard:drama_list')))
        if user.is_marketing or admin or hasattr(user, 'marketing_member'):
            areas.append(('Marketing', reverse('dashboard:marketing_home')))
        if user.is_finance or admin:
            areas.append(('Finance', reverse('dashboard:finance_home')))
        if admin:
            areas.append(('Admin', reverse('dashboard:admin_home')))

    return {
        'brand_name': 'Bonovela',
        'finance_member': getattr(request, 'finance_member', None),
        'marketing_member': getattr(request, 'marketing_member', None),
        'ads_connected': getattr(request, 'ads_connected', True),
        'area': area,
        'nav': _items(nav, path, lv),
        'areas': areas,
    }
