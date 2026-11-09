from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import admin, author, auth, drama, editorial, finance, marketing
from .views import editorial_people as people
from .views import editorial_quality as quality

app_name = 'dashboard'

urlpatterns = [
    path('', auth.home, name='home'),
    path('login/', auth.LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(next_page='dashboard:login'), name='logout'),

    # Author Center
    path('author/become/', author.become_author, name='become_author'),
    path('author/', author.stories, name='author_stories'),
    path('author/stories/new/', author.story_info, name='author_story_new'),
    path('author/stories/<int:pk>/info/', author.story_info, name='author_story_info'),
    path('author/stories/<int:pk>/delete/', author.delete_story, name='author_story_delete'),
    path('author/stories/<int:pk>/chapters/', author.chapters, name='author_chapters'),
    path('author/stories/<int:pk>/chapters/new/', author.chapter_editor, name='author_chapter_new'),
    path('author/stories/<int:pk>/chapters/<int:chapter_id>/', author.chapter_editor, name='author_chapter_edit'),
    path('author/stories/<int:pk>/chapters/<int:chapter_id>/<str:action>/', author.chapter_action,
         name='author_chapter_action'),
    path('author/stories/<int:pk>/stats/', author.stats, name='author_stats'),
    path('author/stories/<int:pk>/signing/', author.signing, name='author_signing'),
    path('author/discover/', author.discover, name='author_discover'),
    path('author/calendar/', author.update_calendar, name='author_calendar'),
    path('author/comments/', author.comments, name='author_comments'),
    path('author/income/', author.income, name='author_income'),

    # Editorial (Editor / Senior Editor / Chief Editor)
    path('editorial/', editorial.home, name='editorial_home'),
    path('editorial/team/', people.team_view, name='editorial_team'),
    path('editorial/team/<int:pk>/', people.editor_detail, name='editorial_editor'),
    path('editorial/authors/', people.authors, name='editorial_authors'),
    path('editorial/authors/<int:pk>/', people.author_detail, name='editorial_author'),
    path('editorial/books/', editorial.books, name='editorial_books'),
    path('editorial/books/<int:pk>/', editorial.book, name='editorial_book'),
    path('editorial/books/<int:pk>/action/', editorial.book_action, name='editorial_book_action'),
    path('editorial/submissions/', editorial.queue, name='editorial_queue'),
    path('editorial/submissions/<int:pk>/', editorial.application, name='editorial_application'),
    path('editorial/completion/', quality.completion_list, name='editorial_completion'),
    path('editorial/completion/<int:pk>/', quality.completion_detail, name='editorial_completion_detail'),
    path('editorial/plagiarism/', quality.plagiarism, name='editorial_plagiarism'),
    path('editorial/duplicates/', quality.duplicates, name='editorial_duplicates'),
    path('editorial/performance/', people.performance, name='editorial_performance'),
    path('editorial/recruitment/', people.recruitment, name='editorial_recruitment'),
    path('editorial/promotions/', editorial.promotions, name='editorial_promotions'),
    path('editorial/shelves/', editorial.shelves, name='editorial_shelves'),
    path('editorial/commission/', people.commission, name='editorial_commission'),
    path('editorial/escalations/', people.escalations, name='editorial_escalations'),
    path('editorial/messages/', people.team_messages, name='editorial_messages'),
    path('editorial/reports/', people.reports, name='editorial_reports'),

    # Drama Studio (drama creators)
    path('drama-studio/', drama.dramas, name='drama_list'),
    path('drama-studio/new/', drama.drama_form, name='drama_new'),
    path('drama-studio/analytics/', drama.stats_all, name='drama_stats_all'),
    path('drama-studio/earnings/', drama.earnings, name='drama_earnings'),
    path('drama-studio/<int:pk>/', drama.episodes, name='drama_episodes'),
    path('drama-studio/<int:pk>/edit/', drama.drama_form, name='drama_edit'),
    path('drama-studio/<int:pk>/action/', drama.drama_action, name='drama_action'),
    path('drama-studio/<int:pk>/stats/', drama.stats, name='drama_stats'),
    path('drama-studio/<int:pk>/episodes/new/', drama.episode_form, name='drama_episode_new'),
    path('drama-studio/<int:pk>/episodes/<int:episode_id>/', drama.episode_form, name='drama_episode_edit'),
    path('drama-studio/<int:pk>/episodes/<int:episode_id>/action/', drama.episode_action,
         name='drama_episode_action'),

    # Marketing
    path('marketing/', marketing.home, name='marketing_home'),
    path('marketing/banners/', marketing.banners, name='marketing_banners'),
    path('marketing/banners/new/', marketing.banner_form, name='marketing_banner_new'),
    path('marketing/banners/<int:pk>/', marketing.banner_form, name='marketing_banner_edit'),
    path('marketing/banners/<int:pk>/action/', marketing.banner_action, name='marketing_banner_action'),
    path('marketing/codes/', marketing.codes, name='marketing_codes'),
    path('marketing/broadcasts/', marketing.broadcasts, name='marketing_broadcasts'),

    # Finance
    path('finance/', finance.home, name='finance_home'),
    path('finance/transactions/', finance.transactions, name='finance_transactions'),
    path('finance/balances/', finance.balances, name='finance_balances'),
    path('finance/payouts/', finance.payouts, name='finance_payouts'),
    path('finance/payouts/generate/', finance.payout_generate, name='finance_payout_generate'),
    path('finance/payouts/<int:pk>/', finance.payout_action, name='finance_payout_action'),
    path('finance/payouts/commission/', finance.book_commission, name='finance_book_commission'),
    path('finance/statements/', finance.statements, name='finance_statements'),

    # Admin
    path('admin-panel/', admin.home, name='admin_home'),
    path('admin-panel/users/', admin.users, name='admin_users'),
    path('admin-panel/users/<int:pk>/', admin.user_action, name='admin_user_action'),
    path('admin-panel/books/', admin.books, name='admin_books'),
    path('admin-panel/books/<int:pk>/', admin.book_action, name='admin_book_action'),
    path('admin-panel/revenue/', admin.money, name='admin_money'),
    path('admin-panel/reports/', admin.reports, name='admin_reports'),
    path('admin-panel/reports/<int:pk>/', admin.report_action, name='admin_report_action'),
    path('admin-panel/store/', admin.store, name='admin_store'),
]
