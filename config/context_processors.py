def admin_nav(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    if not (user.is_staff or user.is_superuser or getattr(user, 'role', '') == 'admin' or getattr(user, 'role', '') == 'course_team'):
        return {}
    from accounts.models import User
    return {'nav_pending_count': User.objects.filter(is_approved=False).count()}
