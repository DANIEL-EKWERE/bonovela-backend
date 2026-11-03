from django.contrib import admin

from . import models

admin.site.register(models.Wallet, list_display=('user', 'coins', 'vouchers'), search_fields=('user__email',))
admin.site.register(models.Transaction, list_display=('user', 'kind', 'title', 'coins', 'bonus_used', 'created_at'),
                    list_filter=('kind',))
admin.site.register(models.CoinPackage, list_display=('coins', 'bonus', 'price_cents', 'tag', 'is_active'))
admin.site.register(models.SubscriptionPlan, list_display=('name', 'price_cents', 'days', 'is_active'))
admin.site.register(models.GiftItem, list_display=('emoji', 'name', 'price', 'votes'))
for m in (models.BonusBatch, models.VoucherEntry, models.ChapterUnlock, models.AutoUnlock, models.Subscription,
          models.Gift, models.Vote, models.AuthorEarning, models.PayoutMethod, models.RedeemCode):
    admin.site.register(m)
