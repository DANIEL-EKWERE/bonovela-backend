from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from . import author_views as author
from . import views as v
from . import wallet_views as w

urlpatterns = [
    path('health/', v.health),
    # Auth
    path('auth/register/', v.RegisterView.as_view()),
    path('auth/email-status/', v.email_status),
    path('auth/token/', TokenObtainPairView.as_view()),
    path('auth/token/refresh/', TokenRefreshView.as_view()),
    path('auth/me/', v.MeView.as_view()),
    path('auth/onboarding/', v.onboarding),
    path('auth/become-author/', v.become_author),
    # Catalogue
    path('taxonomy/', v.taxonomy),
    path('books/', v.BookListView.as_view()),
    path('books/discover/', v.discover),
    path('books/rankings/', v.rankings),
    path('books/<int:pk>/', v.BookDetailView.as_view()),
    path('books/<int:pk>/related/', v.related),
    path('books/<int:pk>/chapters/', v.chapters),
    path('books/<int:pk>/chapters/<int:number>/', v.chapter_detail),
    path('books/<int:pk>/chapters/<int:number>/unlock/', v.unlock),
    path('books/<int:pk>/auto-unlock/', v.auto_unlock),
    path('books/<int:pk>/shelf/', v.shelf),
    path('books/<int:pk>/reviews/', v.ReviewListView.as_view()),
    path('books/<int:pk>/gift/', v.gift),
    path('books/<int:pk>/vote/', v.vote),
    path('reviews/<int:review_id>/like/', v.like_review),
    path('reports/', v.report),
    path('authors/<int:user_id>/', v.author_profile),
    path('authors/<int:user_id>/follow/', v.follow),
    path('following/', v.following),
    # Library
    path('library/', v.library),
    path('history/', v.history),
    # Inbox
    path('inbox/', v.inbox),
    path('inbox/read-all/', v.inbox_read_all),
    path('inbox/<int:message_id>/', v.inbox_message),
    # Wallet & store
    path('wallet/', w.wallet),
    path('wallet/transactions/', w.transactions),
    path('wallet/bonus/', w.bonus_history),
    path('wallet/vouchers/', w.voucher_history),
    path('wallet/unlocked/', w.unlocked),
    path('wallet/top-up/', w.top_up),
    path('wallet/subscribe/', w.subscribe),
    path('wallet/redeem/', w.redeem),
    path('store/', w.store),
    # Rewards
    path('rewards/', w.rewards),
    path('rewards/check-in/', w.check_in),
    path('rewards/tasks/<slug:key>/', w.complete_task),
    path('rewards/reading-time/', w.reading_time),
    path('rewards/reading/<int:minutes>/claim/', w.claim_reading),
    # Author Center
    path('author/stories/', author.StoryListCreate.as_view()),
    path('author/stories/<int:pk>/', author.StoryDetail.as_view()),
    path('author/stories/<int:pk>/chapters/', author.ChapterListCreate.as_view()),
    path('author/stories/<int:pk>/chapters/<int:chapter_id>/', author.ChapterDetail.as_view()),
    path('author/stories/<int:pk>/chapters/<int:chapter_id>/<slug:action>/', author.chapter_action),
    path('author/stories/<int:pk>/application/', author.application),
    path('author/stories/<int:pk>/sign/', author.sign_contract),
    path('author/stories/<int:pk>/stats/', author.stats),
    path('author/comments/', author.comments),
    path('author/comments/<int:review_id>/reply/', author.reply),
    path('author/income/', author.income),
    path('author/contests/', author.contests),
]
