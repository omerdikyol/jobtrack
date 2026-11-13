import base64

from jobtrack.gmail_client import find_job_url, parse_message
from jobtrack.models import Classification
from jobtrack.store import Store
from tests.helpers import make_message


def test_find_job_url():
    want = "https://www.linkedin.com/jobs/view/4012345678/"
    assert find_job_url('<a href="https://www.linkedin.com/comm/jobs/view/4012345678/?trackingId=x&amp;a=1">') == want
    assert find_job_url("https://linkedin.com/jobs/view/data-engineer-at-acme-4012345678?x=1") == want
    assert find_job_url('<a href="https://example.com/jobs/view/4012345678">') == ""


def test_parse_message_reads_linkedin_html_only():
    html = '<p>hi</p><a href="https://www.linkedin.com/comm/jobs/view/4012345678/">Job</a>'
    raw = lambda sender: {
        "id": "m", "threadId": "t",
        "payload": {
            "headers": [{"name": "From", "value": sender}],
            "mimeType": "multipart/alternative",
            "parts": [{"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(html.encode()).decode()}}],
        },
    }
    assert parse_message(raw("LinkedIn <jobs-noreply@linkedin.com>")).job_url.endswith("/4012345678/")
    assert parse_message(raw("Someone <a@example.com>")).job_url == ""


def test_job_url_links_follow_up_to_application(tmp_path):
    url = "https://www.linkedin.com/jobs/view/4012345678/"
    with Store(tmp_path / "t.db") as store:
        first = make_message("applied", message_id="a", thread_id="t1")
        first.job_url = url
        _, app_id = store.record(first, Classification(kind="applied", score=9, company="Acme", role="Data Engineer"))
        # Reworded title and different thread: only the URL ties them.
        second = make_message("update", message_id="b", thread_id="t2")
        second.job_url = url
        outcome, matched = store.record(second, Classification(kind="viewed", score=9, company="Acme", role="Totally Different Title"))
        assert (outcome, matched) == ("matched", app_id)
        assert store.get_application(app_id).job_url == url


def test_linkedin_company_named_like_a_sentence():
    body = (
        "Başvurunuz Your Software Supplier şirketine gönderildi\n"
        "Co-Founder (Remote & Flexible Hours)\n"
        "Your Software Supplier · Avrupa, Ortadoğu ve Afrika (Uzaktan)\n"
    )
    msg = make_message(
        "Ömer, başvurunuz Your Software Supplier şirketine gönderildi",
        body,
        sender="LinkedIn <jobs-noreply@linkedin.com>",
    )
    from jobtrack.classify import classify

    result = classify(msg)
    assert result.company == "Your Software Supplier"
    assert result.role == "Co-Founder (Remote & Flexible Hours)"


def test_restart_abandons_stale_sync_of_same_pid(tmp_path):
    from jobtrack.jobs import SyncRunner

    path = tmp_path / "t.db"
    with Store(path) as store:
        run_id = store.begin_sync("q")  # owner_pid == our pid, as after a container restart
    SyncRunner(str(path), settings=object())
    with Store(path) as store:
        assert store.begin_sync("again") > run_id  # no SyncBusyError
