from django import forms
from django.forms import inlineformset_factory

from .models import Assignment, Question, Solution


class AssignmentForm(forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ('title', 'num_questions')


class GACreateForm(forms.Form):
    title = forms.CharField(max_length=100)
    num_questions = forms.IntegerField(
        min_value=1, max_value=12,
        widget=forms.Select(choices=[(i, i) for i in range(1, 13)],
                            attrs={'onchange': 'this.form.submit()'}),
    )

    def __init__(self, *args, n=0, **kwargs):
        super().__init__(*args, **kwargs)
        for i in range(1, n + 1):
            self.fields[f'key_{i}'] = forms.CharField(
                max_length=50, required=False,
                label=str(i),
                widget=forms.TextInput(attrs={'placeholder': 'question id', 'style': 'width: 220px'}),
            )


class QuestionForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = ('number', 'key', 'order')
        widgets = {
            'key': forms.TextInput(attrs={'placeholder': 'question id', 'style': 'width: 220px'}),
            'number': forms.HiddenInput(),
            'order': forms.HiddenInput(),
        }


QuestionFormSet = inlineformset_factory(
    Assignment,
    Question,
    form=QuestionForm,
    extra=0,
    min_num=0,
    validate_min=False,
)


class SolutionForm(forms.ModelForm):
    class Meta:
        model = Solution
        fields = ('content_md',)
        labels = {'content_md': 'Your solution'}
        widgets = {
            'content_md': forms.Textarea(attrs={
                'rows': 20,
                'placeholder': 'Write your step-by-step solution here…',
                'class': 'md-editor',
            }),
        }

    def clean_content_md(self):
        text = (self.cleaned_data.get('content_md') or '').strip()
        if not text:
            raise forms.ValidationError('Please write your solution before submitting.')
        return text
