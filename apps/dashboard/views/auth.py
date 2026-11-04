from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


class LoginView(auth_views.LoginView):
    template_name = 'dashboard/login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        if form.get_user().is_suspended:
            messages.error(self.request, 'This account is suspended.')
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
        return redirect('dashboard:drama_list')
    if user.is_marketing:
        return redirect('dashboard:marketing_home')
    if user.is_finance:
        return redirect('dashboard:finance_home')
    return redirect('dashboard:author_stories')
