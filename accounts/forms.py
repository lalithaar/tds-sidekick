from django import forms
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm as DjangoAuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.text import slugify
import uuid

from .models import User


class LoginForm(DjangoAuthenticationForm):
    username = forms.CharField(
        label='Email or username',
        widget=forms.TextInput(attrs={'autofocus': True, 'autocomplete': 'username'}),
    )


class SignupForm(UserCreationForm):
    full_name = forms.CharField(max_length=100, required=True, label='Full Name')
    email = forms.EmailField(required=True, label='Student Email ID')

    class Meta:
        model = User
        fields = ('full_name', 'email', 'password1', 'password2')

    def clean_email(self):
        email = self.cleaned_data.get('email', '').strip().lower()
        if not email.endswith('@ds.study.iitm.ac.in'):
            raise ValidationError('Only @ds.study.iitm.ac.in email addresses are allowed.')
        return email

    def clean_full_name(self):
        return self.cleaned_data.get('full_name', '').strip()

    def save(self, commit=True):
        user = super().save(commit=False)
        user.display_name = self.cleaned_data['full_name']
        user.email = self.cleaned_data['email'].strip().lower()
        base = slugify(user.email.split('@')[0]) or 'user'
        username = f'{base}-{uuid.uuid4().hex[:6]}'
        while User.objects.filter(username=username).exists():
            username = f'{base}-{uuid.uuid4().hex[:6]}'
        user.username = username
        user.is_approved = False
        user.is_active = True
        user.role = User.Role.PARTICIPANT
        user.generate_approval_key()
        if commit:
            user.save()
        return user
