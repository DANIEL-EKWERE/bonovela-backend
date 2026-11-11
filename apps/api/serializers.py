from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db.models import Avg
from rest_framework import serializers

from apps.catalog.models import Chapter, Contest, Genre, Story, Tag, word_count
from apps.editorial.models import ContractApplication
from apps.engagement.models import InboxMessage, Review, ReviewReply
from apps.wallet import services
from apps.wallet.models import BonusBatch, CoinPackage, GiftItem, SubscriptionPlan, Transaction, VoucherEntry

User = get_user_model()


# ── Accounts ─────────────────────────────────────────────────────────────────
class UserSerializer(serializers.ModelSerializer):
    name = serializers.CharField(source='display_name', read_only=True)
    followers = serializers.SerializerMethodField()
    following = serializers.SerializerMethodField()
    is_author = serializers.BooleanField(read_only=True)
    is_plus = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            'id', 'email', 'username', 'name', 'pen_name', 'about', 'avatar', 'role', 'is_author', 'provider',
            'email_verified', 'reader_type', 'language', 'followers', 'following', 'is_plus',
        ]
        read_only_fields = ['id', 'email', 'role', 'provider', 'email_verified']

    def get_followers(self, obj):
        return obj.follower_set.count()

    def get_following(self, obj):
        return obj.following_set.count()

    def get_is_plus(self, obj):
        return services.has_plus(obj)


class RegisterSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    name = serializers.CharField(required=False, allow_blank=True)

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('This email is already signed up. Please sign in.')
        return value

    def validate_password(self, value):
        # App rules: 6–18 chars, letters and numbers, underscores allowed.
        import re
        if not (6 <= len(value) <= 18 and re.search(r'[A-Za-z]', value) and re.search(r'\d', value)
                and re.fullmatch(r'[A-Za-z0-9_]+', value)):
            raise serializers.ValidationError(
                '6–18 characters, with both letters and numbers; underscores are the only symbol allowed.'
            )
        validate_password(value)
        return value

    def create(self, data):
        username = data['email'].split('@')[0]
        base, n = username, 2
        while User.objects.filter(username=username).exists():
            username, n = f'{base}{n}', n + 1
        return User.objects.create_user(
            username=username, email=data['email'], password=data['password'], pen_name=data.get('name', '')
        )


# ── Catalogue ────────────────────────────────────────────────────────────────
class BookSerializer(serializers.ModelSerializer):
    """Matches the app's BookModel.fromJson."""

    author = serializers.CharField(source='author.display_name')
    author_id = serializers.IntegerField(source='author.id')
    genre = serializers.CharField(source='genre.name', default='')
    tags = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    rating = serializers.SerializerMethodField()
    chapter_count = serializers.IntegerField(read_only=True)
    word_count = serializers.IntegerField(read_only=True)
    is_completed = serializers.SerializerMethodField()
    age_rating = serializers.SerializerMethodField()

    class Meta:
        model = Story
        fields = [
            'id', 'slug', 'title', 'author', 'author_id', 'synopsis', 'genre', 'tags', 'cover_url', 'cover_index',
            'rating', 'views', 'chapter_count', 'word_count', 'is_completed', 'is_short', 'age_rating', 'language',
            'updated_at',
        ]

    def get_tags(self, obj):
        return [t.name for t in obj.tags.all()]

    def get_cover_url(self, obj):
        if not obj.cover:
            return ''
        request = self.context.get('request')
        return request.build_absolute_uri(obj.cover.url) if request else obj.cover.url

    def get_rating(self, obj):
        avg = getattr(obj, 'avg_stars', None)
        if avg is None:
            avg = obj.reviews.filter(is_hidden=False).aggregate(a=Avg('stars'))['a']
        return round((avg or 4.5) * 2, 1)  # app shows ratings out of 10

    def get_is_completed(self, obj):
        return obj.status == Story.STATUS_COMPLETED

    def get_age_rating(self, obj):
        return int((obj.content_rating or '16+').rstrip('+'))


class ChapterListSerializer(serializers.ModelSerializer):
    """Matches the app's ChapterModel (without content)."""

    book_id = serializers.IntegerField(source='story_id')
    title = serializers.CharField(source='display_title')
    is_locked = serializers.SerializerMethodField()
    coin_price = serializers.IntegerField(source='price')

    class Meta:
        model = Chapter
        fields = ['id', 'book_id', 'number', 'title', 'word_count', 'is_locked', 'coin_price', 'published_at']

    def get_is_locked(self, obj):
        return not services.can_read(self.context['request'].user, obj)


class ChapterDetailSerializer(ChapterListSerializer):
    content = serializers.SerializerMethodField()
    note = serializers.SerializerMethodField()

    class Meta(ChapterListSerializer.Meta):
        fields = ChapterListSerializer.Meta.fields + ['content', 'note']

    def get_content(self, obj):
        return obj.content if services.can_read(self.context['request'].user, obj) else ''

    def get_note(self, obj):
        return obj.note if services.can_read(self.context['request'].user, obj) else ''


class ReviewReplySerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source='user.display_name', read_only=True)
    is_author = serializers.SerializerMethodField()

    class Meta:
        model = ReviewReply
        fields = ['id', 'user_name', 'is_author', 'text', 'created_at']

    def get_is_author(self, obj):
        return obj.user_id == obj.review.story.author_id


class ReviewSerializer(serializers.ModelSerializer):
    """Matches the app's CommentModel."""

    book_id = serializers.IntegerField(source='story_id', read_only=True)
    user_name = serializers.CharField(source='user.display_name', read_only=True)
    date = serializers.DateTimeField(source='created_at', read_only=True)
    likes = serializers.SerializerMethodField()
    is_mine = serializers.SerializerMethodField()
    replies = ReviewReplySerializer(many=True, read_only=True)

    class Meta:
        model = Review
        fields = ['id', 'book_id', 'user_name', 'stars', 'text', 'date', 'likes', 'is_mine', 'replies']

    def validate_stars(self, value):
        if not 1 <= value <= 5:
            raise serializers.ValidationError('Stars must be 1–5.')
        return value

    def get_likes(self, obj):
        return obj.likes.count()

    def get_is_mine(self, obj):
        user = self.context['request'].user
        return user.is_authenticated and obj.user_id == user.id


class GenreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Genre
        fields = ['id', 'name', 'slug', 'show_in_app']


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ['id', 'name', 'group']


class ContestSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contest
        fields = ['id', 'title', 'brief', 'prize', 'deadline']


# ── Wallet ───────────────────────────────────────────────────────────────────
class TransactionSerializer(serializers.ModelSerializer):
    price = serializers.CharField(read_only=True)
    book_id = serializers.IntegerField(source='story_id', allow_null=True)
    chapter = serializers.IntegerField(source='chapter.number', default=None)

    class Meta:
        model = Transaction
        fields = ['id', 'kind', 'title', 'coins', 'bonus_used', 'price', 'book_id', 'chapter', 'created_at']


class BonusBatchSerializer(serializers.ModelSerializer):
    remaining = serializers.IntegerField(read_only=True)
    expired = serializers.IntegerField(read_only=True)
    is_expired = serializers.BooleanField(read_only=True)

    class Meta:
        model = BonusBatch
        fields = ['id', 'source', 'amount', 'consumed', 'remaining', 'expired', 'is_expired', 'received_at', 'expires_at']


class VoucherEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = VoucherEntry
        fields = ['id', 'source', 'amount', 'created_at']


class CoinPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = CoinPackage
        fields = ['id', 'coins', 'bonus', 'price_cents', 'tag']


class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'name', 'price_cents', 'old_price_cents', 'period', 'days', 'billing_note', 'badge']


class GiftItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = GiftItem
        fields = ['id', 'name', 'emoji', 'price', 'votes']


class InboxSerializer(serializers.ModelSerializer):
    book_id = serializers.IntegerField(source='story_id', allow_null=True)
    read = serializers.BooleanField(source='is_read')
    date = serializers.DateTimeField(source='created_at')

    class Meta:
        model = InboxMessage
        fields = ['id', 'title', 'body', 'date', 'book_id', 'read']


# ── Author tools ─────────────────────────────────────────────────────────────
class AuthorStorySerializer(serializers.ModelSerializer):
    """Novel Information, as edited in Author Center."""

    tags = serializers.SlugRelatedField(many=True, slug_field='name', queryset=Tag.objects.all(), required=False)
    genre = serializers.SlugRelatedField(slug_field='name', queryset=Genre.objects.filter(is_active=True), allow_null=True,
                                         required=False)
    is_info_complete = serializers.BooleanField(read_only=True)
    can_apply_for_contract = serializers.BooleanField(read_only=True)
    chapter_count = serializers.SerializerMethodField()
    word_count = serializers.SerializerMethodField()
    application_status = serializers.SerializerMethodField()

    class Meta:
        model = Story
        fields = [
            'id', 'title', 'synopsis', 'cover', 'cover_index', 'language', 'audience', 'content_rating',
            'novel_type', 'genre', 'tags', 'status', 'is_short', 'is_signed', 'is_info_complete',
            'can_apply_for_contract', 'chapter_count', 'word_count', 'application_status', 'views', 'created_at',
            'updated_at',
        ]
        read_only_fields = ['is_signed', 'views', 'created_at', 'updated_at']

    def validate_language(self, value):
        if value not in Story.LANGUAGES:
            raise serializers.ValidationError(f"Books can be written in {', '.join(Story.LANGUAGES)} for now.")
        return value

    def validate_synopsis(self, value):
        lo, hi = settings.BONOVELA['SYNOPSIS_WORDS']
        n = word_count(value)
        if value and not lo <= n <= hi:
            raise serializers.ValidationError(f'Synopsis must be {lo}–{hi} words (now {n}).')
        return value

    def validate_is_short(self, value):
        if self.instance and self.instance.is_signed and value != self.instance.is_short:
            raise serializers.ValidationError("Signed books can't change length.")
        return value

    def validate_tags(self, value):
        if len(value) > 10:
            raise serializers.ValidationError('Up to 10 tags.')
        return value

    def get_chapter_count(self, obj):
        return obj.published_chapters().count()

    def get_word_count(self, obj):
        return obj.total_words

    def get_application_status(self, obj):
        app = obj.applications.first()
        return app.status if app else None


class AuthorChapterSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chapter
        fields = [
            'id', 'number', 'title', 'content', 'note', 'status', 'word_count', 'scheduled_for', 'published_at',
            'updated_at',
        ]
        read_only_fields = ['number', 'status', 'word_count', 'published_at', 'updated_at']

    def validate_content(self, value):
        # Drafts may be short; nothing may exceed the maximum, and a live
        # chapter must stay within the range.
        live = bool(self.instance and self.instance.status == Chapter.STATUS_PUBLISHED)
        error = Chapter.length_error(word_count(value), publishing=live)
        if error:
            raise serializers.ValidationError(error)
        return value


class ApplicationSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ContractApplication
        fields = ['id', 'story', 'status', 'status_label', 'message', 'contract_type', 'feedback', 'legal_name',
                  'chosen_editor', 'created_at', 'updated_at']
        read_only_fields = ['story', 'status', 'contract_type', 'feedback', 'chosen_editor', 'created_at', 'updated_at']
