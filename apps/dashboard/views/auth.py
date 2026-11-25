from django.contrib import messages
from django.contrib.auth import logout, views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

# Drama Studio is closed for now: drama creators can't sign in to the dashboard.
DRAMA_CLOSED = 'Drama Studio is closed for now.'


class LoginView(auth_views.LoginView):
    template_name = 'dashboard/login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        if form.get_user().is_suspended:
            messages.error(self.request, 'This account is suspended.')
            return redirect('dashboard:login')
        if form.get_user().is_drama_creator:
            messages.error(self.request, DRAMA_CLOSED)
            return redirect('dashboard:login')
        from apps.users.emails import needs_verification, send_verification
        if needs_verification(form.get_user()):
            send_verification(form.get_user())
            messages.error(self.request, 'Please confirm your email first. We just sent you a new activation link.')
            return redirect('dashboard:login')
        response = super().form_valid(form)
        from apps.users.ip import record
        record(form.get_user(), self.request, 'login')
        return response


@login_required
def home(request):
    """Send each role to its own area."""
    user = request.user
    if user.is_platform_admin:
        return redirect('dashboard:admin_home')
    if user.is_editor:
        return redirect('dashboard:editorial_home')
    if user.is_drama_creator:
        # Drama Studio is closed for now; was: redirect('dashboard:drama_list')
        logout(request)
        messages.error(request, DRAMA_CLOSED)
        return redirect('dashboard:login')
    if user.is_marketing:
        return redirect('dashboard:marketing_home')
    if user.is_finance:
        return redirect('dashboard:finance_home')
    return redirect('dashboard:author_stories')


# ── Account confirmation & password reset (links from noreply@bonovela.com) ──
def _user_from(uidb64):
    from django.utils.encoding import force_str
    from django.utils.http import urlsafe_base64_decode

    from apps.users.models import User
    try:
        return User.objects.get(pk=force_str(urlsafe_base64_decode(uidb64)))
    except (User.DoesNotExist, ValueError, TypeError, OverflowError):
        return None


def verify_email(request, uidb64, token):
    from django.shortcuts import render

    from apps.users.emails import verify_token
    user = _user_from(uidb64)
    ok = bool(user) and (user.email_verified or verify_token.check_token(user, token))
    if ok and not user.email_verified:
        user.email_verified = True
        user.save(update_fields=['email_verified'])
    return render(request, 'dashboard/account_message.html', {
        'title': 'Account activated 🎉' if ok else 'Link expired',
        'message': 'Your Bonovela account is active. Open the Bonovela app and sign in.' if ok else
        'This activation link is no longer valid. Open the app and sign in to get a new one.',
    })


def password_reset_request(request):
    """Web: 'Forgot password?' — email a reset link."""
    from django.shortcuts import render

    from apps.users.emails import send_password_reset
    from apps.users.models import User
    sent = False
    if request.method == 'POST':
        user = User.objects.filter(email__iexact=request.POST.get('email', '').strip(), is_active=True).first()
        if user:
            send_password_reset(user)
        sent = True  # same answer either way: don't reveal who has an account
    return render(request, 'dashboard/password_reset.html', {'sent': sent})


def password_reset_confirm(request, uidb64, token):
    from django.contrib.auth.forms import SetPasswordForm
    from django.contrib.auth.tokens import default_token_generator
    from django.shortcuts import render
    user = _user_from(uidb64)
    valid = bool(user) and default_token_generator.check_token(user, token)
    form = SetPasswordForm(user, request.POST or None) if valid else None
    if valid and request.method == 'POST' and form.is_valid():
        form.save()
        if not user.email_verified:  # the link proves they own the address
            user.email_verified = True
            user.save(update_fields=['email_verified'])
        return render(request, 'dashboard/account_message.html', {
            'title': 'Password changed', 'message': 'You can now sign in with your new password.'})
    return render(request, 'dashboard/password_reset_confirm.html', {'form': form, 'valid': valid})
