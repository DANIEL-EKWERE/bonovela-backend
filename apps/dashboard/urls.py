from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import admin, author, auth

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
# Editorial (AE/SE/CE) screens live in views/editorial.py but are not routed
# until their designs are final.
