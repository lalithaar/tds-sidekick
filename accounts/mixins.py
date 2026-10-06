from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import redirect


class ApprovedRequiredMixin(LoginRequiredMixin):
    login_url = '/accounts/login/'
    permission_denied_message = 'Account pending approval.'

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not getattr(request.user, 'is_approved', False):
            return redirect('accounts:pending_notice')
        return super().dispatch(request, *args, **kwargs)
