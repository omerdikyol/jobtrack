"""Company extraction against real-world sender and subject shapes.

Every case here reproduces a message shape that was previously mis-extracted,
so the expected value is the employer, never the recruiter, the platform, or a
sentence fragment. Employers and senders are fictional but keep the structure
that matters: the ATS relay domain, the "Hiring Team" display name, the
`people.<domain>` subdomain, and the concatenated brand ("SolisHR").
"""

import pytest

from jobtrack.classify import classify, extract_company
from tests.helpers import make_message

CASES = [
    # (sender, subject, expected employer)
    (
        '"Halden P&O Talent Acquisition" <talentacquisition.x@halden.com>',
        "Follow your application status, Sam",
        "Halden P&O",
    ),
    (
        "Riley Okonkwo - Cobalt <riley.okonkwo@cobalt.teamtailor-mail.com>",
        "Software Engineer at Cobalt - Thank you for your application",
        "Cobalt",
    ),
    (
        "Dana Whitfield - Norvale <dana.whitfield@norvale.teamtailor-mail.com>",
        "Norvale - Update about your application",
        "Norvale",
    ),
    (
        "Petra Lindqvist - Quorix <petra.lindqvist@quorix.teamtailor-mail.com>",
        "Thank you for your application!",
        "Quorix",
    ),
    (
        "Liam Porter - Foldwell <liam.porter@foldwell.teamtailor-mail.com>",
        "Thank you for your job application!",
        "Foldwell",
    ),
    (
        "Jordan Reyes <jordan.reyes@meridian-networks.com>",
        "(No-reply) Thank You for Applying to Meridian Networks!",
        "Meridian Networks",
    ),
    (
        "Lumen Works <no-reply@ashbyhq.com>",
        "Update on your Lumen Works application",
        "Lumen Works",
    ),
    (
        "Northgate <no-reply@ashbyhq.com>",
        "We've received your application! ✅",
        "Northgate",
    ),
    (
        "Trellis <no-reply@ashbyhq.com>",
        "We've received your application to join Trellis! ✈️",
        "Trellis",
    ),
    (
        "Halo <no-reply@ashbyhq.com>",
        "Your Halo journey just started, Sam 🚀",
        "Halo",
    ),
    (
        "Kestrel <noreply@successfactors.eu>",
        "Thank you for applying",
        "Kestrel",
    ),
    (
        "Brightmoor <system@successfactors.eu>",
        "Thank you for your application to Full Stack Software Engineer",
        "Brightmoor",
    ),
    (
        "Kirkby Talent Acquisition <KirkbyTalentAcquisition-NoReply@kirkbygroup.com>",
        "Complete your application for job: Back-End Developer",
        "Kirkby",
    ),
    (
        "Nordvale DoNotReply <donotreply.nordvaleME@people.nordvale.com>",
        "Nordvale - Thank you for your application for Software Engineer",
        "Nordvale",
    ),
    (
        "Cardinal <no-reply@cardinal.dev>",
        "We've Received Your Application – Cardinal",
        "Cardinal",
    ),
    (
        "Sandbar <no-reply@hire.lever.co>",
        "Your application to Sandbar has been received! 🦄",
        "Sandbar",
    ),
    (
        "Maple Systems <no-reply@us.greenhouse-mail.io>",
        "Important information about your application to Maple Systems",
        "Maple Systems",
    ),
    (
        "Vantage <no-reply@eu.greenhouse-mail.io>",
        "Thank you for applying to Vantage!",
        "Vantage",
    ),
    (
        "Gridworks <no-reply@us.greenhouse-mail.io>",
        "Thank you for applying to Gridworks",
        "Gridworks",
    ),
    (
        "Kite <no-reply@us.greenhouse-mail.io>",
        "Thank you for applying to Kite",
        "Kite",
    ),
    (
        "Tamsin <noreply@candidates.workablemail.com>",
        "Thanks for applying to Tamsin",
        "Tamsin",
    ),
    (
        "Cadence <noreply@candidates.workablemail.com>",
        "Thanks for applying to Cadence",
        "Cadence",
    ),
    ("Evrid <noreply@msg.jobylon.com>", "An update on your application", "Evrid"),
    ("Solis HR <solishr@mail.manatal.com>", "Your Application to", "Solis"),
    (
        "Aster <no-reply@hire.eu.lever.co>",
        "Thank you for your application to Aster",
        "Aster",
    ),
    (
        "Harbor <hiring@harbor.app>",
        "Your application at Harbor - received successfully!",
        "Harbor",
    ),
    ("Fernwood <jobs@fernwood.dev>", "Application for Junior Software Engineer (Node.js)", "Fernwood"),
    (
        "no-reply@sentinel-group.com",
        "Thanks for you application for the Software Developer position",
        "Sentinel Group",
    ),
]

# Real messages carry the signal in the body; these tests only replayed the
# subject, so supply a plausible body to exercise the full path.
BODY = "Thanks for applying. We have received your application and will be in touch."


@pytest.mark.parametrize("sender, subject, expected", CASES)
def test_employer_is_extracted_not_the_platform_or_a_person(sender, subject, expected):
    message = make_message(subject, sender=sender)
    assert extract_company(message) == expected


@pytest.mark.parametrize("sender, subject, _expected", CASES)
def test_real_messages_are_still_classified(sender, subject, _expected):
    result = classify(make_message(subject, body=BODY, sender=sender))
    assert result is not None, "real job mail must not be dropped"
    assert result.company


@pytest.mark.parametrize(
    "sender, subject",
    [
        ("LinkedIn <jobs-noreply@linkedin.com>", "5 new jobs match your search"),
        (
            '"LinkedIn İş İlanı Uyarıları" <jobalerts-noreply@linkedin.com>',
            "New jobs for you at Acme and 20 others",
        ),
        ("LinkedIn <security-noreply@linkedin.com>", "New sign-in to your account"),
        ("LinkedIn <billing-noreply@linkedin.com>", "Your receipt from LinkedIn"),
    ],
)
def test_linkedin_platform_mail_is_never_tracked(sender, subject):
    """LinkedIn only ever sends us alerts and notices — never an application."""
    assert classify(make_message(subject, sender=sender)) is None


def test_a_platform_relay_with_no_employer_anywhere_is_not_named_greenhouse():
    message = make_message(
        "Application update", sender="no-reply@eu.greenhouse-mail.io"
    )
    assert extract_company(message) is None
    assert classify(message) is None


@pytest.mark.parametrize(
    "subject,sender,expected",
    [
        (
            "Thanks for applying to Halcyon AI!",
            "Halcyon AI Hiring Team <no-reply@ashbyhq.com>",
            "Halcyon AI",
        ),
        (
            "Information about your application to the position of Backend Engineer, ClickHouse at Vantage",
            "no-reply@eu.greenhouse-mail.io",
            "Vantage",
        ),
    ],
)
def test_company_names_with_business_suffix_and_title_at_employer(
    subject, sender, expected
):
    assert extract_company(make_message(subject, sender=sender)) == expected


@pytest.mark.parametrize(
    "subject,snippet,expected",
    [
        (
            "Invitation to Interview for: AI Platform Dev",
            "Apply for free — no Connects needed.",
            "AI Platform Dev",
        ),
        (
            "Application for Junior Software Engineer (Node.js)",
            "",
            "Junior Software Engineer (Node.js)",
        ),
        (
            "Information about your application to the position of Backend Engineer, ClickHouse at Vantage",
            "",
            "Backend Engineer, ClickHouse",
        ),
        (
            "Software Engineer at Cobalt - Thank you for your application",
            "",
            "Software Engineer",
        ),
        (
            "Your application update",
            "Thank you for your interest in our AI Application Engineer (518814) position.",
            "AI Application Engineer (518814)",
        ),
        (
            "Application update",
            "Your application for the position &quot;AI Native Software Engineer&quot; has been received.",
            "AI Native Software Engineer",
        ),
        (
            "Application update",
            "Your application for Network Software Engineer job was submitted successfully.",
            "Network Software Engineer",
        ),
        ("Feedback on your Back End Engineer application", "", "Back End Engineer"),
        ("Invitation to interview", "Apply for free — no Connects needed.", None),
    ],
)
def test_real_role_titles_are_clean_not_sentence_fragments(subject, snippet, expected):
    from jobtrack.classify import extract_role

    assert extract_role(make_message(subject, snippet=snippet)) == expected


def test_work_location_is_not_an_employer():
    message = make_message('Your application for Software Engineer, SaaS at Hybrid Cluj-Napoca',sender='Redwood Systems, Inc. @ icims <jobs@icims.com>')
    assert extract_company(message) == 'Redwood Systems, Inc'


def test_employer_with_ai_suffix_is_not_also_a_role():
    from jobtrack.classify import extract_role
    message = make_message("Thanks for applying to Halcyon AI!",sender="Halcyon AI Hiring Team <no-reply@ashbyhq.com>")
    assert extract_role(message) is None
