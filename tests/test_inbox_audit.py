"""Misses found by replaying the stored inbox (2026-11-05), anonymised."""

import pytest

from jobtrack.classify import classify
from jobtrack.models import Classification
from jobtrack.store import Store
from tests.helpers import make_message


@pytest.mark.parametrize(
    "subject, body, expected",
    [
        ("Your Application to Acme", "While we truly appreciate your interest and effort, "
         "we have made the decision not to move forward at this time.", "rejected"),
        ("Important information about your application to Acme", "We reviewed your "
         "application carefully. Unfortunately, we won't be moving it forward at this time.", "rejected"),
        ("An update on your Acme application", "Thanks for applying. This time, we've "
         "decided to take other candidates forward.", "rejected"),
        ("Your Acme interview results", "Thank you for completing your interview. After reviewing "
         "your submission, we weren't able to match your profile with our current project requirements.", "rejected"),
        ("We're sorry", "Thank you for applying. We've made the decision not to take your "
         "application further, on this occasion.", "rejected"),
        ("Başvurunuz Hakkında — Backend Developer Pozisyonu", "Başvurunuzu dikkatle değerlendirdik. "
         "Mevcut pozisyon için farklı bir profille ilerleme kararı aldık.", "rejected"),
        ("Acme / Game Developer - Başvurun elimize ulaştı!", "Game Developer pozisyonu için yaptığın "
         "başvurun elimize ulaştı.", "applied"),
        ("Internal Tools Engineer - Acme", "Acme'ye gösterdiğin ilgi için çok teşekkür ederiz! "
         "Başvurunun olumlu değerlendirilmesi durumunda seninle iletişimde olacağız.", "applied"),
        ("Thank you for completing your application: Back-End Developer (110)",
         "Thank you for completing your application with the requested information.", "applied"),
        ("Your recent job application for Software Engineer", "We received your job application "
         "for Software Engineer. We will review your application and get back to you soon.", "applied"),
        ("Thank you from Acme", "After assessing the needs of this team, the tough decision has "
         "been made not to continue forward at this time.", "rejected"),
        ("Research Engineer • Remote • Acme x AI Labs", "I'm from Hurdles, a global recruitment firm. "
         "We would like to refer you for an opportunity. Application process: resume evaluation & interview stage.",
         "outreach"),
    ],
)
def test_kind(subject, body, expected):
    assert classify(make_message(subject, body)).kind == expected


@pytest.mark.parametrize(
    "subject, body, sender, company, role",
    [
        # Role text must stop where the sentence carries on.
        ("Update on your Dune application", "We wanted to let you know that the role of Software "
         "Engineer - Data Foundation, which you applied to, has just been filled.",
         "Dune Team <no-reply@ashbyhq.com>", "Dune", "Software Engineer - Data Foundation"),
        ("We have received your application!", "We will review your application for Product "
         "Engineer shortly, and get back to you as soon as possible.",
         "Will - Bolt Insight <will@boltinsight.com>", "Bolt Insight", "Product Engineer"),
        # Assessment platforms relay for an employer, who is named in the text.
        ("Sezzle Inc.: Please Complete Our Assessment for Software Engineer II",
         "Sezzle Inc. is inviting you to complete an assessment.",
         "noreply@mg.wonderlic.com", "Sezzle Inc", None),
        ("Important: your assessment is waiting", "Just a reminder that you have an assessment "
         "to complete for PIWorks. Start assessment",
         "TestGorilla Assessments <assessments@testgorilla.com>", "PIWorks", None),
        ("Sezzle | ACTION REQUIRED - Wonderlic Assessment", "Please complete the Wonderlic assessment.",
         "Paula Rojas <paula@sezzle.com>", "Sezzle", None),
        ("Request from BCG X has expired.", "Your coding assessment request expired.",
         "CodeSignal <noreply@codesignal.com>", "BCG X", None),
        # Sender display names that are not an employer.
        ("Amaris Consulting Recruitment Process", "We decided not to go ahead with your application.",
         "NoReply <noreply@amaris.com>", "Amaris Consulting", None),
        ("BCG Application Confirmation", "We've received your application for the role of "
         "Forward Deployed AI Engineer.", "Careers at BCG <careers@talent.bcg.com>", "BCG",
         "Forward Deployed AI Engineer"),
        ("Ön Değerlendirme Süreci Bilgilendirme", "Sevgili Deniz, Backend Developer (Full Stack) - Hybrid "
         "pozisyonuna gösterdiğin ilgi için teşekkür ederiz. Başvuruna olumlu dönüş yapamıyoruz.",
         '"Pegasus İşe Alım" <apply@flypgs.com>', "Pegasus", "Backend Developer (Full Stack) - Hybrid"),
        ("Software Engineer 3246 at Akamai Technologies", "Regarding the Software Engineer (Job ID: 3246), you have not been selected for this position.",
         "Talent Acquisition <hr@akamai.com>", "Akamai Technologies", "Software Engineer 3246"),
        ("Bir adım daha yaklaştınız: DHL Group'ta R&D Specialist ID 333359",
         "DHL Group'un DHL Express alanında R&D Specialist ID 333359 pozisyonuna başvurunuz alındı.",
         "DHL Recruiting - DO NOT REPLY! <noreply@dhl.com>", "DHL", "R&D Specialist ID 333359"),
        ("Papcorns - Jr Backend Engineer İlanı için Başvurunuzu Aldı", "Başvurunuzu aldık.",
         "Papcorns <hr@papcorns.com>", "Papcorns", "Jr Backend Engineer"),
        ("Software Engineer | $120/hr Remote | Micro1 x AI Labs", "I'm from Hurdles, a global "
         "recruitment firm. We would like to refer you.", "Hurdles <jobs@hurdles.com>", "Hurdles",
         "Software Engineer"),
    ],
)
def test_company_and_role(subject, body, sender, company, role):
    result = classify(make_message(subject, body, sender=sender))
    assert (result.company, result.role) == (company, role)


def record(store, message_id, company, role, kind="applied"):
    return store.record(
        make_message("Update", message_id=message_id, thread_id=message_id),
        Classification(kind=kind, score=12, company=company, role=role),
    )[1]


def test_followups_continue_the_application_they_answer(tmp_path):
    with Store(tmp_path / "t.db") as store:
        akamai = record(store, "a1", "Akamai Technologies", "Software Engineer II")
        assert record(store, "a2", "Akamai", None, "incomplete") == akamai
        dune = record(store, "d1", "Dune", "Software Engineer")
        assert record(store, "d2", "Dune", "Software Engineer - Data Foundation", "rejected") == dune
        chain = record(store, "c1", "Chainlink Labs", "Software Engineer I, CCIP")
        assert record(store, "c2", "Chainlink Labs", "Senior Software Engineer CCIP", "rejected") == chain
        # A second rejection after the first one is a second application.
        assert record(store, "c3", "Chainlink Labs", "Back End Engineer", "rejected") != chain
        # Different job numbers are different postings, even at one company.
        record(store, "k1", "Kirey", "Developer 110")
        assert len(store.list_applications()) == 5
        record(store, "k2", "Kirey", "Developer 120", "rejected")
        assert len(store.list_applications()) == 6
        # Two open applications: a different title must not be guessed into either.
        record(store, "t1", "Turkcell", "Java Developer")
        record(store, "t2", "Turkcell", "Full Stack Developer")
        assert len(store.list_applications()) == 8
        record(store, "t3", "Turkcell", "AI Engineer", "rejected")
        assert len(store.list_applications()) == 9


def record_mail(store, message_id, subject, body, sender, company, role, kind):
    return store.record(
        make_message(subject, body, sender=sender, message_id=message_id, thread_id=message_id),
        Classification(kind=kind, score=12, company=company, role=role),
    )[1]


def test_assessment_platform_mail_joins_the_employer_named_in_its_text(tmp_path):
    with Store(tmp_path / "t.db") as store:
        app = record(store, "s1", "Sezzle", "Software Engineer II")
        # The platform is not the employer; the text names a tracked company.
        assert record_mail(store, "s2", "Complete your coding test", "Sezzle has invited you "
                           "to take a test.", "CodeSignal <no-reply@codesignal.com>",
                           "CodeSignal", None, "assessment") == app
        # A company the user never applied to is not invented from the text.
        other = record_mail(store, "s3", "Complete your coding test", "Nordvale has invited "
                            "you to take a test.", "CodeSignal <no-reply@codesignal.com>",
                            None, None, "assessment")
        assert other != app


def test_untitled_followups_are_placed_by_text_or_by_the_invitation_sender(tmp_path):
    with Store(tmp_path / "t.db") as store:
        architect = record(store, "b1", "BCG", "AI Tech Architect")
        deployed = record(store, "b2", "BCG", "Forward Deployed AI Engineer")
        # The text names one of the company's roles.
        assert record_mail(store, "b3", "Next steps", "Your coding test for the Forward "
                           "Deployed AI Engineer role is ready.", "Giorgia <g@bcg.com>",
                           "BCG", None, "assessment") == deployed
        # A reminder from the same address continues that application.
        assert record_mail(store, "b4", "Recruiting update", "We haven't received your test.",
                           "Giorgia <g@bcg.com>", "BCG", None, "assessment") == deployed
        assert store.get_application(architect).status == "applied"
        # A mail stating its own, different title stays a separate job.
        record_mail(store, "b5", "Application received", "Similar jobs: Forward Deployed AI "
                    "Engineer", "Careers <c@bcg.com>", "BCG", "Data Scientist", "applied")
        assert len(store.list_applications()) == 3


def test_a_different_job_at_the_only_open_application_stays_separate(tmp_path):
    with Store(tmp_path / "t.db") as store:
        app = record(store, "p1", "P.I. Works", "Part-Time SW Product Design Engineer")
        assert record(store, "p2", "P.I. Works", "Product Design Intern (Telecom Software)",
                      "assessment") != app
