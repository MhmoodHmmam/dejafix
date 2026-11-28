"""Input validation for the review pages. Every field is explicit (no mass assignment)."""

from __future__ import annotations

from django import forms

from support.models import MAX_ARTICLE_LENGTH, MAX_ARTICLE_TITLE_LENGTH, MAX_NOTE_LENGTH, KnowledgeGap, Ticket


class ApproveForm(forms.Form):
    note = forms.CharField(max_length=MAX_NOTE_LENGTH, required=False, widget=forms.Textarea(attrs={"rows": 2}))


class RejectForm(forms.Form):
    note = forms.CharField(
        max_length=MAX_NOTE_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="What was wrong. It becomes a learning the next resolver reads.",
        error_messages={"required": "A rejection needs a note explaining what was wrong."},
    )


class RetireForm(forms.Form):
    note = forms.CharField(
        max_length=MAX_NOTE_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Why this fix is wrong or outdated. Resolvers stop reusing it and read your reason instead.",
        error_messages={"required": "Retiring a fix needs a reason."},
    )


class GapDraftForm(forms.ModelForm):
    class Meta:
        model = KnowledgeGap
        fields = ["draft_title", "draft_body"]
        labels = {"draft_title": "Article title", "draft_body": "Article body (Markdown)"}
        widgets = {"draft_body": forms.Textarea(attrs={"rows": 16})}

    draft_title = forms.CharField(max_length=MAX_ARTICLE_TITLE_LENGTH, required=False)
    draft_body = forms.CharField(max_length=MAX_ARTICLE_LENGTH, required=False, widget=forms.Textarea(attrs={"rows": 16}))


class ListFilterForm(forms.Form):
    """`?status=` (whitelisted per list) and `?page=`."""

    page = forms.IntegerField(min_value=1, required=False)

    def __init__(self, data, *, statuses):
        super().__init__(data)
        self.fields["status"] = forms.ChoiceField(choices=[("", "All"), *statuses], required=False)


def ticket_filter(data) -> ListFilterForm:
    return ListFilterForm(data, statuses=Ticket.Status.choices)


def gap_filter(data) -> ListFilterForm:
    return ListFilterForm(data, statuses=KnowledgeGap.Status.choices)
