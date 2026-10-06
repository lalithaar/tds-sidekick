from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None
        User = get_user_model()
        lookup = username.strip()
        user = (
            User.objects.filter(Q(email__iexact=lookup) | Q(username=lookup))
            .order_by('pk')
            .first()
        )
        if user and user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
