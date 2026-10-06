from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
import secrets
import string


def gen_approval_key() -> str:
    part1 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    part2 = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))
    return f'{part1}-{part2}'


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        PARTICIPANT = 'participant', 'Participant'

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PARTICIPANT)
    display_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)

    is_approved = models.BooleanField(default=False)
    approval_key = models.CharField(max_length=16, unique=True, blank=True, null=True)
    approval_requested_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='approved_users',
    )

    def generate_approval_key(self):
        if self.approval_key:
            return self.approval_key
        key = gen_approval_key()
        while User.objects.filter(approval_key=key).exists():
            key = gen_approval_key()
        self.approval_key = key
        self.approval_requested_at = timezone.now()
        return key

    def __str__(self):
        return self.display_name or self.username
