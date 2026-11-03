from django.contrib import messages
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse


class LoginView(auth_views.LoginView):
    template_name = 'dashboard/login.html'
    redirect_authenticated_user = True

    def form_valid(self, form):
        if form.get_user().is_suspended:
            messages.error(self.request, 'This account is suspended.')
            return redirect('dashboard:login')
        return super().form_valid(form)


@login_required
def home(request):
    """Send each role to its own area."""
    user = request.user
    if user.is_platform_admin:
        return redirect('dashboard:admin_home')
    if user.is_editor:
        try:
            return redirect(reverse('dashboard:editorial_home'))
        except NoReverseMatch:  # editorial area not routed yet
            pass
    return redirect('dashboard:author_stories')
