from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView

from .forms import SignupForm, LoginForm
from .models import User


class LoginView(DjangoLoginView):
    template_name = 'registration/login.html'
    authentication_form = LoginForm


class SignupView(CreateView):
    model = User
    form_class = SignupForm
    template_name = 'accounts/signup.html'
    success_url = reverse_lazy('accounts:pending')

    def form_valid(self, form):
        user = form.save(commit=False)
        if not user.approval_key:
            user.generate_approval_key()
        user.save()
        login(self.request, user)
        return redirect('accounts:pending')


signup_view = SignupView.as_view()


@login_required
def pending_view(request):
    if getattr(request.user, 'is_approved', False):
        return redirect('core:home')
    if not request.user.approval_key:
        request.user.generate_approval_key()
        request.user.save(update_fields=['approval_key', 'approval_requested_at'])
    return render(request, 'accounts/pending.html', {'user': request.user})


@login_required
def pending_notice(request):
    if getattr(request.user, 'is_approved', False):
        return redirect('core:home')
    return render(request, 'accounts/pending_notice.html')


def _is_admin(user):
    return user.is_authenticated and (user.is_staff or user.is_superuser or getattr(user, 'role', 'participant') == 'admin')


@login_required
def pending_approvals(request):
    if not _is_admin(request.user):
        return redirect('core:home')
    pending = User.objects.filter(is_approved=False).order_by('approval_requested_at')
    approved = User.objects.filter(is_approved=True).order_by('-approved_at')
    return render(
        request,
        'accounts/approvals.html',
        {'pending': pending, 'approved': approved},
    )


@login_required
def approve_user(request, pk):
    if not _is_admin(request.user):
        return redirect('core:home')
    if request.method != 'POST':
        return redirect('accounts:pending_approvals')
    u = get_object_or_404(User, pk=pk)
    if not u.is_approved:
        u.is_approved = True
        u.approved_at = timezone.now()
        u.approved_by = request.user
        u.approval_key = None
        u.save(update_fields=['is_approved', 'approved_at', 'approved_by', 'approval_key'])
    return redirect('accounts:pending_approvals')
