"""Drama Studio: drama creators upload series and episodes, and track plays
and earnings."""

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Max, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.catalog.models import Tag
from apps.drama.models import Drama, Episode, EpisodeUnlock
from apps.users.models import User
from apps.wallet.models import AuthorEarning, Payout, PayoutMethod

from ..forms import DramaForm, EpisodeForm, PayoutForm
from .access import role_area

creator_area = role_area(User.ROLE_CREATOR)


def _mine(request):
    qs = Drama.objects.all() if request.user.is_platform_admin else Drama.objects.filter(creator=request.user)
    return qs.annotate(
        n_episodes=Count('episodes', distinct=True),
        n_published=Count('episodes', filter=Q(episodes__status=Episode.STATUS_PUBLISHED), distinct=True),
        n_saves=Count('saves', distinct=True),
    )


def _drama(request, pk):
    return get_object_or_404(_mine(request), pk=pk)


@creator_area
def dramas(request):
    qs = _mine(request).select_related('genre', 'story')
    f = request.GET.get('f', 'all')
    if f == 'live':
        qs = qs.filter(is_published=True, is_removed=False)
    elif f == 'draft':
        qs = qs.filter(is_published=False)
    totals = qs.aggregate(plays=Sum('plays'))
    return render(request, 'dashboard/drama/list.html', {'dramas': qs, 'f': f, 'total_plays': totals['plays'] or 0})


@creator_area
def drama_form(request, pk=None):
    drama = _drama(request, pk) if pk else Drama(creator=request.user)
    form = DramaForm(request.POST or None, request.FILES or None, instance=drama)
    if request.method == 'POST' and form.is_valid():
        drama = form.save()
        if request.FILES.get('cover'):
            drama.cover_index = -1
            drama.save(update_fields=['cover_index'])
        messages.success(request, f'"{drama.title}" saved.')
        return redirect('dashboard:drama_episodes', drama.pk)
    return render(request, 'dashboard/drama/form.html', {
        'drama': drama, 'form': form, 'palettes': range(8),
        'tag_groups': [{'label': label, 'tags': Tag.objects.filter(group=key)} for key, label in Tag.GROUP_CHOICES],
        'selected_tags': {int(x) for x in request.POST.getlist('tags') if x.isdigit()} if request.method == 'POST'
        else set(drama.tags.values_list('id', flat=True)) if drama.pk else set(),
    })


@creator_area
@require_POST
def drama_action(request, pk):
    drama = _drama(request, pk)
    action = request.POST.get('action')
    if action == 'publish':
        if not drama.published_episodes().exists():
            messages.error(request, 'Publish at least one episode first.')
        elif not drama.synopsis.strip():
            messages.error(request, 'Add a synopsis before going live.')
        else:
            drama.publish()
            messages.success(request, f'"{drama.title}" is live in the app 🎬')
    elif action == 'unpublish':
        drama.is_published = False
        drama.save(update_fields=['is_published', 'updated_at'])
        messages.success(request, 'Taken off the app. Viewers who saved it keep it on their list.')
    elif action == 'delete':
        title = drama.title
        drama.delete()
        messages.success(request, f'Deleted "{title}".')
        return redirect('dashboard:drama_list')
    return redirect('dashboard:drama_episodes', drama.pk)


@creator_area
def episodes(request, pk):
    drama = _drama(request, pk)
    eps = drama.episodes.annotate(n_unlocks=Count('unlocks'))
    next_number = (drama.episodes.aggregate(n=Max('number'))['n'] or 0) + 1
    return render(request, 'dashboard/drama/episodes.html', {
        'drama': drama, 'episodes': eps, 'next_number': next_number,
        'form': EpisodeForm(instance=Episode(drama=drama), initial={'number': next_number}),
        'free': settings.BONOVELA['FREE_EPISODES'], 'price': settings.BONOVELA['EPISODE_PRICE'],
        'drafts': drama.episodes.filter(status=Episode.STATUS_DRAFT).count(),
    })


@creator_area
def episode_form(request, pk, episode_id=None):
    drama = _drama(request, pk)
    episode = get_object_or_404(Episode, pk=episode_id, drama=drama) if episode_id else Episode(drama=drama)
    form = EpisodeForm(request.POST or None, request.FILES or None, instance=episode,
                       initial=None if episode_id else {'number': (drama.episodes.aggregate(n=Max('number'))['n'] or 0) + 1})
    if request.method == 'POST' and form.is_valid():
        episode = form.save(commit=False)
        if not episode.has_video:
            form.add_error('video', 'Upload a video or paste a video link.')
        else:
            episode.save()
            if request.POST.get('action') == 'publish':
                episode.publish()
                messages.success(request, f'Episode {episode.number} published.')
            else:
                messages.success(request, f'Episode {episode.number} saved as a draft.')
            return redirect('dashboard:drama_episodes', drama.pk)
    return render(request, 'dashboard/drama/episode_form.html', {'drama': drama, 'episode': episode, 'form': form})


@creator_area
@require_POST
def episode_action(request, pk, episode_id):
    drama = _drama(request, pk)
    action = request.POST.get('action')
    if action == 'publish_all':
        drafts = [e for e in drama.episodes.filter(status=Episode.STATUS_DRAFT) if e.has_video]
        for e in drafts:
            e.publish()
        messages.success(request, f'Published {len(drafts)} episodes.')
        return redirect('dashboard:drama_episodes', drama.pk)
    ep = get_object_or_404(Episode, pk=episode_id, drama=drama)
    if action == 'publish':
        if not ep.has_video:
            messages.error(request, 'This episode has no video yet.')
        else:
            ep.publish()
            messages.success(request, f'Episode {ep.number} published.')
    elif action == 'unpublish':
        ep.status = Episode.STATUS_DRAFT
        ep.save(update_fields=['status'])
        messages.success(request, f'Episode {ep.number} moved to drafts.')
    elif action == 'delete':
        ep.delete()
        messages.success(request, 'Episode deleted.')
    return redirect('dashboard:drama_episodes', drama.pk)


def _stats(dramas):
    unlocks = EpisodeUnlock.objects.filter(episode__drama__in=dramas)
    spent = unlocks.aggregate(c=Sum('coins_paid'), b=Sum('bonus_paid'))
    return {
        'plays': sum(d.plays for d in dramas),
        'saves': sum(d.n_saves for d in dramas),
        'unlocks': unlocks.count(),
        'coins': (spent['c'] or 0) + (spent['b'] or 0),
    }


@creator_area
def stats(request, pk):
    drama = _drama(request, pk)
    eps = list(drama.published_episodes().annotate(n_unlocks=Count('unlocks')))
    first = eps[0].plays if eps and eps[0].plays else 1
    for e in eps:
        e.retention = round(e.plays * 100 / first)
    return render(request, 'dashboard/drama/stats.html', {
        'drama': drama, 'episodes': eps, 'totals': _stats([drama]),
        'finish_rate': eps[-1].retention if eps else 0,
    })


@creator_area
def stats_all(request):
    dramas = list(_mine(request).order_by('-plays'))
    return render(request, 'dashboard/drama/stats_all.html', {'dramas': dramas, 'totals': _stats(dramas)})


@creator_area
def earnings(request):
    earned = AuthorEarning.objects.filter(author=request.user)
    payouts = Payout.objects.filter(author=request.user)
    paid = payouts.filter(status=Payout.STATUS_PAID).aggregate(t=Sum('amount_cents'))['t'] or 0
    pending = payouts.filter(status=Payout.STATUS_PENDING).aggregate(t=Sum('amount_cents'))['t'] or 0
    total = earned.aggregate(t=Sum('amount_cents'))['t'] or 0

    method = PayoutMethod.objects.filter(author=request.user).first()
    form = PayoutForm(request.POST or None, instance=method)
    if request.method == 'POST' and form.is_valid():
        obj = form.save(commit=False)
        obj.author = request.user
        obj.save()
        messages.success(request, 'Payment info saved.')
        return redirect('dashboard:drama_earnings')
    return render(request, 'dashboard/drama/earnings.html', {
        'total': total, 'paid': paid, 'pending': pending, 'balance': total - paid - pending,
        'recent': earned[:30], 'payouts': payouts[:20], 'method': method, 'form': form,
        'share': int(settings.BONOVELA['CREATOR_SHARE'] * 100),
        'minimum': settings.BONOVELA['PAYOUT_MINIMUM_CENTS'],
    })
