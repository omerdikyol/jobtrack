"""Turkish and English phrasings taken (anonymised) from real inbox misses."""

import pytest

from jobtrack.classify import classify
from jobtrack.gmail_client import _extract_body, default_query
from tests.helpers import make_message


@pytest.mark.parametrize(
    "subject, body, expected",
    [
        # Turkish rejections
        (
            "Ön Değerlendirme Süreci Bilgilendirme",
            "Backend Developer pozisyonuna gösterdiğin ilgi için teşekkür ederiz. "
            "Yaptığımız değerlendirmeler sonucunda başvuruna şu an olumlu dönüş yapamıyoruz.",
            "rejected",
        ),
        # Same, typed without Turkish characters
        (
            "On Degerlendirme Sureci",
            "Basvuruna su an olumlu donus yapamiyoruz.",
            "rejected",
        ),
        # English rejections that also thank the reader for applying
        (
            "Update on your Acme application",
            "Thank you again for taking the time to apply. The role of Software "
            "Engineer, which you applied to, has just been filled.",
            "rejected",
        ),
        (
            "Thank you for your job application!",
            "We have reviewed your application, and unfortunately we have decided "
            "not to progress with your candidacy on this occasion.",
            "rejected",
        ),
        (
            "Your application @ Acme",
            "Thanks for applying. At this time we have chosen to move forward with "
            "other candidates that more closely match the background we need.",
            "rejected",
        ),
        # Turkish confirmations
        (
            "Papcorns - Jr Backend Engineer İlanı için Başvurunuzu Aldı",
            "Başvurunuz alındı! Şirketimize gösterdiğiniz ilgi için teşekkür ederiz.",
            "applied",
        ),
        (
            "Yanıtların İletildi",
            "Sevgili Ömer, Yanıtların başarılı bir şekilde tarafımıza iletilmiştir. "
            "Pegasus İşe Alım Ekibi",
            "applied",
        ),
        (
            "Acme Full-stack Engineer başvurunuz",
            "Pozisyonuna yaptığınız başvuruyu aldık ve ekibimiz değerlendirme sürecinde.",
            "applied",
        ),
        # Unfinished applications
        (
            "Başvuru Tamamlama",
            "Öncelikle ilgin için teşekkürler. Başvurunu tamamlamak için tıkla.",
            "incomplete",
        ),
        # Assessment invitations beat the "your application" wording
        (
            "Your application for Product Design Intern at Acme",
            "Hi, you're invited to take an assessment for the Product Design Intern role.",
            "assessment",
        ),
        (
            "Important: your assessment is waiting",
            "Just a reminder that you have an assessment to complete for Acme.",
            "assessment",
        ),
    ],
)
def test_bilingual_lifecycle_events(subject, body, expected):
    result = classify(make_message(subject, body))
    assert result is not None and result.kind == expected


@pytest.mark.parametrize(
    "subject, body, sender",
    [
        # LinkedIn job recommendations mention "interview" in job titles
        (
            "Luron AI, Inc.",
            "Sizin için seçilen iş ilanlarını görüntüleyin. Backend Engineer, AI Interview Engine",
            "LinkedIn <jobs-noreply@linkedin.com>",
        ),
        (
            "Sana uygun ilanlar burada!",
            "Aşağıdaki ilanlar başvuru için seni bekliyor.",
            "Kariyer.net <aday@e.kariyer.net>",
        ),
        (
            "Action Required: Update your candidate profile consent",
            "The data retention period for your candidate profile is ending. "
            "You applied for a role at Acme in the past.",
            "Acme <no-reply@us.greenhouse-mail.io>",
        ),
        (
            "welcome to google ai plus",
            "Power your job search with resume help and interview prep.",
            "Google <googleone-noreply@google.com>",
        ),
        # "interview" in a tracking link is not an invitation
        (
            "You have a new message",
            "Your application inbox: https://example.com/proposals/interview/123",
            "Board <noreply@example.com>",
        ),
    ],
)
def test_alerts_and_noise_are_not_events(subject, body, sender):
    assert classify(make_message(subject, body, sender=sender)) is None


def test_conditional_rejection_wording_is_not_a_rejection():
    result = classify(
        make_message(
            "Thank you for your application to Acme",
            "Thanks for applying! Our team will review your application. "
            "If you are not selected for this position, keep an eye on our jobs page.",
        )
    )
    assert result is not None and result.kind == "applied"


def test_html_labelled_as_plain_text_is_stripped():
    import base64

    html = "<!DOCTYPE html><html><body><p>Hello <b>there</b></p></body></html>"
    payload = {
        "mimeType": "text/plain",
        "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()},
    }
    assert _extract_body(payload) == "Hello there"


def test_default_query_reaches_turkish_and_body_only_mail():
    query = default_query()
    for needle in ('"başvurunuzu"', '"pozisyonuna"', "from:workablemail.com", '"regret to inform"'):
        assert needle in query
