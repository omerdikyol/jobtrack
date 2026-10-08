import pytest

from jobtrack.classify import classify, extract_company, extract_role, is_job_related
from tests.helpers import make_message


# --------------------------------------------------------------------------
# Event classification
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "subject, body, expected",
    [
        (
            "Thank you for applying to Stripe",
            "We have received your application and will be in touch.",
            "applied",
        ),
        (
            "Application received - Software Engineer",
            "Your application was successfully submitted.",
            "applied",
        ),
        (
            "Interview invitation at Acme",
            "We'd love to schedule a call to discuss next steps with the team.",
            "interview",
        ),
        (
            "Your HackerRank test for Acme",
            "Please complete the online assessment within five days.",
            "assessment",
        ),
        (
            "We are pleased to offer you the position",
            "Attached is your offer letter and the compensation package details.",
            "offer",
        ),
        (
            "Update on your application to Acme",
            "Unfortunately, we have decided to move forward with other candidates.",
            "rejected",
        ),
        (
            "Regarding your candidacy",
            "We regret to inform you that you were not selected for this position.",
            "rejected",
        ),
        (
            "Opportunity at Northwind",
            "I came across your profile and would be open to a quick chat about a role.",
            "outreach",
        ),
    ],
)
def test_classifies_lifecycle_events(subject, body, expected):
    result = classify(make_message(subject, body))
    assert result is not None, f"expected a classification for {subject!r}"
    assert result.kind == expected


def test_rejection_beats_application_confirmation_wording():
    # "your application" alone is weak; an explicit rejection must win.
    result = classify(
        make_message(
            "Your application to Acme",
            "Unfortunately we are not moving forward with your application.",
        )
    )
    assert result.kind == "rejected"


def test_offer_beats_rejection_when_both_present():
    result = classify(
        make_message(
            "Your offer letter",
            "We are pleased to offer you the role. Unfortunately the start date is fixed.",
        )
    )
    assert result.kind == "offer"


# --------------------------------------------------------------------------
# Relevance gate
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "subject, body",
    [
        ("Your order has shipped", "Track your package with the tracking number."),
        ("Meeting notes", "Let's discuss the roadmap on Tuesday."),
        ("Your invoice is ready", "Payment received, thank you."),
    ],
)
def test_ignores_non_job_mail(subject, body):
    assert classify(make_message(subject, body)) is None


def test_generic_job_mail_without_signal_is_skipped():
    assert classify(make_message("Hello", "Just checking in about the role.")) is None


def test_ats_sender_counts_as_relevant():
    message = make_message(
        "Update from our team",
        "See the details inside.",
        sender="Greenhouse <no-reply@greenhouse.io>",
    )
    assert is_job_related(message)


@pytest.mark.parametrize(
    "subject",
    [
        "Thanks for applying to Acme",
        "Applied for the Backend role",
        "You apply, we reply",
        "Your application to Acme",
        "Applicant update",
    ],
)
def test_apply_applying_applied_all_count_as_job_vocabulary(subject):
    """'applic\\w+' alone misses the apply/applying/applied family."""
    assert is_job_related(make_message(subject, sender="jobs@acme.com"))


@pytest.mark.parametrize("subject", ["Apple newsletter", "Applause for our new album"])
def test_apple_and_applause_are_not_job_vocabulary(subject):
    assert not is_job_related(make_message(subject, sender="news@example.com"))


# --------------------------------------------------------------------------
# Company extraction
# --------------------------------------------------------------------------
def test_company_from_subject():
    message = make_message(
        "Thank you for applying to Stripe",
        sender="Greenhouse <no-reply@greenhouse.io>",
    )
    assert extract_company(message) == "Stripe"


def test_company_from_display_name():
    message = make_message(
        "We received your application",
        sender="Stripe Recruiting <recruiting@stripe.com>",
    )
    assert extract_company(message) == "Stripe"


def test_company_from_domain():
    message = make_message(
        "Application received",
        sender="jobs@northwind.com",
    )
    assert extract_company(message) == "Northwind"


def test_ats_display_name_is_not_mistaken_for_company():
    message = make_message(
        "Application Received - Software Engineer - Acme Corp",
        sender="Greenhouse <no-reply@greenhouse.io>",
    )
    assert extract_company(message) == "Acme Corp"


def test_company_via_ats_display_name():
    message = make_message(
        "Your application",
        sender="Acme Corp via Greenhouse <no-reply@greenhouse.io>",
    )
    assert extract_company(message) == "Acme Corp"


def test_company_stopwords_rejected():
    message = make_message(
        "Thank you for your interest",
        sender="no-reply@example.com",
    )
    assert extract_company(message) == "Example"


def test_company_from_assessment_subject_naming_employer():
    # The ATS display name is useless here, so the company must come from the subject.
    message = make_message(
        "Your online assessment for Acme Corp",
        sender="HackerRank <no-reply@hackerrank.com>",
    )
    assert extract_company(message) == "Acme Corp"


def test_company_from_opportunity_subject_beats_recruiter_display_name():
    message = make_message(
        "Opportunity at Initech",
        sender="Sarah Lee <sarah.lee@talentpartners.com>",
    )
    assert extract_company(message) == "Initech"


# --------------------------------------------------------------------------
# Role extraction
# --------------------------------------------------------------------------
def test_role_from_position_wording():
    message = make_message(
        "We received your application for the Software Engineer position"
    )
    assert extract_role(message) == "Software Engineer"


def test_role_from_subject_segments():
    message = make_message("Application Received - Backend Developer - Acme Corp")
    assert extract_role(message) == "Backend Developer"


def test_role_absent_when_not_mentioned():
    message = make_message("Thank you for applying to Stripe")
    assert extract_role(message) is None


@pytest.mark.parametrize(
    "title",
    ["Interview Process", "AI Platform Dev", "React: Dashboard UI", "Project Setup."],
)
def test_upwork_invitation_uses_full_explicit_job_name(title):
    msg = make_message(
        f"Invitation to Interview for: {title}",
        "Your profile says Senior Software Engineer.",
        sender="Upwork <donotreply@upwork.com>",
    )
    assert extract_role(msg) == title
    result = classify(msg)
    assert result.kind == "interview"
    assert result.role == title


def test_upwork_invitation_heading_in_body():
    msg = make_message(
        "You have an invitation",
        "#### Invitation to Interview for: Interview Process\nAccept the invitation.",
        sender="Upwork <notify@notifications.upwork.com>",
    )
    assert extract_role(msg) == "Interview Process"


@pytest.mark.parametrize(
    "sender, text",
    [
        ("notify@upwork.com", "Invitation to Interview for:\nSoftware Engineer"),
        (
            "notify@upwork.com.evil.example",
            "Invitation to Interview for: Interview Process",
        ),
        ("recruiter@example.com", "Invitation to Interview for: Interview Process"),
    ],
)
def test_explicit_upwork_title_requires_upwork_sender_and_nonempty_heading(
    sender, text
):
    from jobtrack.classify import upwork_invitation_role

    assert upwork_invitation_role(make_message(text, sender=sender)) is None
