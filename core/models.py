from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify
import uuid


class Assignment(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        POLLING = 'polling', 'Polling (Opt-in)'
        ASSIGNED = 'assigned', 'Assigned'
        CLOSED = 'closed', 'Closed'

    title = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    num_questions = models.PositiveIntegerField()
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='created_assignments')
    created_at = models.DateTimeField(auto_now_add=True)
    poll_opened_at = models.DateTimeField(null=True, blank=True)
    poll_closes_at = models.DateTimeField(null=True, blank=True)
    assigned_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('-created_at',)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title) or 'assignment'
            self.slug = f'{base}-{uuid.uuid4().hex[:6]}'
            while Assignment.objects.filter(slug=self.slug).exists():
                self.slug = f'{base}-{uuid.uuid4().hex[:6]}'
        super().save(*args, **kwargs)

    def is_poll_expired(self):
        if self.status != self.Status.POLLING or not self.poll_closes_at:
            return False
        return timezone.now() >= self.poll_closes_at

    def __str__(self):
        return self.title


class Question(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='questions')
    number = models.PositiveIntegerField()
    key = models.CharField(max_length=50, blank=True, default='')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('assignment', 'number')
        ordering = ('assignment', 'number')

    def __str__(self):
        return f'Q{self.number}' + (f' ({self.key})' if self.key else '')


class Participation(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='participations')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='participations')
    opted_in_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('assignment', 'user')
        ordering = ('-opted_in_at',)


class AssignmentSlot(models.Model):
    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='slots')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='assigned_slots')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='slots')

    class Meta:
        unique_together = ('assignment', 'user', 'question')
        indexes = [
            models.Index(fields=('assignment', 'user')),
            models.Index(fields=('assignment', 'question')),
        ]


class Solution(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft'
        SUBMITTED = 'submitted', 'Submitted'
        NEEDS_FIX = 'needs_fix', 'Needs Fix'
        VERIFIED = 'verified', 'Verified'

    assignment = models.ForeignKey(Assignment, on_delete=models.CASCADE, related_name='solutions')
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='solutions')
    solver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='solutions')
    content_md = models.TextField()
    content_html = models.TextField(blank=True, editable=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    verifier_count = models.PositiveIntegerField(default=0)
    needs_fix_count = models.PositiveIntegerField(default=0)
    last_reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('assignment', 'question', 'solver')
        ordering = ('-submitted_at', 'id')


class Validation(models.Model):
    solution = models.ForeignKey(Solution, on_delete=models.CASCADE, related_name='validations')
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='reviews_given')
    is_working = models.BooleanField()
    comment = models.TextField(blank=True)
    reviewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('solution', 'reviewer')
        ordering = ('reviewed_at',)


class SolutionAttachment(models.Model):
    solution = models.ForeignKey(Solution, on_delete=models.CASCADE, related_name='attachments')
    file = models.FileField(upload_to='solutions/%Y/%m/%d/')
    original_name = models.CharField(max_length=255)
    uploaded_at = models.DateTimeField(auto_now_add=True)
