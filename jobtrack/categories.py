"""Application categories inferred from clear freelance evidence."""

import re
from email.utils import parseaddr

CATEGORIES = {"employment", "freelance"}
PLATFORMS = (
    "upwork.com",
    "freelancer.com",
    "fiverr.com",
    "peopleperhour.com",
    "contra.com",
    "guru.com",
    "malt.com",
)


def infer_category(sender: str, subject: str = "", snippet: str = "") -> str:
    domain = parseaddr(sender)[1].rsplit("@", 1)[-1].lower()
    if any(
        domain == platform or domain.endswith("." + platform) for platform in PLATFORMS
    ):
        return "freelance"
    if re.search(r"\b(?:freelance|freelancing)\b", subject, re.I):
        return "freelance"
    return "employment"


def identity_key(company_key: str, category: str) -> str:
    # Preserve the legacy unique company/role constraint while separating work.
    return ("freelance:" if category == "freelance" else "") + company_key
