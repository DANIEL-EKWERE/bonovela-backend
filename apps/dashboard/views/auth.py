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
