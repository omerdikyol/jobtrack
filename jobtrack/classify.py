"""Turn a raw email into a job-application event.

The classifier is deliberately rule-based: it is transparent, offline, and easy
to tune. Two questions are answered for every message:

1. *Is this about a job application at all?*  (relevance gate)
2. *What happened?*  (lifecycle event: applied / assessment / interview /
   rejected / offer / recruiter outreach)

Company and role names are pulled out with subject-first heuristics, falling
back to the sender's display name and finally the domain.
"""

from __future__ import annotations

import html
import re

from jobtrack.models import Classification, EmailMessage

# --------------------------------------------------------------------------
# Signal tables
# --------------------------------------------------------------------------
# Each signal is (pattern, weight, label). Weights are tuned so that a single
# unambiguous phrase (e.g. "we regret to inform you") is enough on its own,
# while vague wording needs corroboration. A match in the subject line counts
# double, because that is where the real message usually lives.

SIGNALS: dict[str, list[tuple[str, int, str]]] = {
    "rejected": [
        (
            r"başvurunuzla (?:devam etmeyeceğiz|ilerlemeyeceğiz)|başvurunuz (?:reddedildi|olumsuz)",
            6,
            "application declined (Turkish)",
        ),
        (r"\bwe regret\b", 4, "we regret"),
        (r"\bunfortunately\b", 3, "unfortunately"),
        (r"\bnot (?:be )?(?:moving|proceeding|advancing)\b", 4, "not moving forward"),
        (
            r"\bdecided to (?:move forward|proceed|pursue) with (?:other|another)\b",
            5,
            "moving forward with other candidates",
        ),
        (r"\bpursue other (?:candidates|applicants)\b", 5, "pursue other candidates"),
        (r"\bother (?:candidates|applicants)\b", 3, "other candidates"),
        (r"\bnot (?:a|the) (?:right|best) fit\b", 4, "not the right fit"),
        (r"\bnot selected\b", 4, "not selected"),
        (r"\bnot moving forward\b", 4, "not moving forward"),
        (r"\bunsuccessful\b", 4, "unsuccessful"),
        (r"\bposition has been filled\b", 4, "position filled"),
        (r"\bno longer under consideration\b", 5, "no longer under consideration"),
        (
            r"\bwill not be (?:extending|proceeding|moving)\b",
            4,
            "will not be proceeding",
        ),
        (r"\bkeep your (?:resume|cv|details) on file\b", 3, "keep on file"),
        (
            r"\b(?:application|candidacy) (?:was|has been) (?:declined|rejected|unsuccessful)\b",
            5,
            "application declined",
        ),
        (
            r"\bwish you (?:the best|well|luck) in your (?:job )?search\b",
            3,
            "wish you luck in your search",
        ),
        (r"\bdecided not to (?:move|proceed|advance)\b", 4, "decided not to proceed"),
        (r"\bnot (?:to )?advance\b", 3, "not advancing"),
    ],
    "offer": [
        (r"\bjob offer\b", 6, "job offer"),
        (r"\boffer letter\b", 6, "offer letter"),
        (
            r"\b(?:pleased|happy|delighted|excited|thrilled) to offer\b",
            6,
            "pleased to offer",
        ),
        (r"\bwe(?:'d| would) like to offer\b", 6, "would like to offer"),
        (r"\bwelcome (?:you )?to the team\b", 4, "welcome to the team"),
        (r"\bverbal offer\b", 5, "verbal offer"),
        (r"\bformal offer\b", 5, "formal offer"),
        (r"\bcompensation package\b", 3, "compensation package"),
        (r"\byour offer\b", 3, "your offer"),
        (r"\baccept (?:our|the) offer\b", 5, "accept the offer"),
        (r"\boffer to join\b", 5, "offer to join"),
        (r"\bstart date\b", 2, "start date"),
    ],
    "assessment": [
        (r"\bcoding challenge\b", 6, "coding challenge"),
        (r"\bonline assessment\b", 6, "online assessment"),
        (
            r"\btake[- ]home (?:assignment|test|exercise|challenge)\b",
            6,
            "take-home assignment",
        ),
        (r"\bhackerrank\b", 5, "hackerrank"),
        (r"\bcodility\b", 5, "codility"),
        (r"\bcodesignal\b", 5, "codesignal"),
        (r"\bhackerearth\b", 5, "hackerearth"),
        (
            r"\btechnical (?:assessment|evaluation|exercise)\b",
            5,
            "technical assessment",
        ),
        (r"\bskills? (?:test|assessment)\b", 4, "skills assessment"),
        (
            r"\bcomplete(?: the following)? (?:assessment|challenge|exercise)\b",
            4,
            "complete assessment",
        ),
        (r"\bcode sample\b", 3, "code sample"),
    ],
    "interview": [
        (r"\binterview(?:s|ing)?\b", 4, "interview"),
        (r"\bphone screen\b", 5, "phone screen"),
        (r"\btechnical screen\b", 5, "technical screen"),
        (
            r"\bschedule (?:a|an|your) (?:call|chat|conversation|interview|time)\b",
            4,
            "schedule a call",
        ),
        (r"\byour availability\b", 4, "your availability"),
        (r"\b(?:book|pick) a time\b", 4, "pick a time"),
        (r"\bnext (?:step|round|stage)s?\b", 3, "next steps"),
        (r"\bmeet (?:with )?(?:the|our) (?:team|hiring manager)\b", 3, "meet the team"),
        (r"\bspeak (?:with|to) (?:you|the team)\b", 3, "speak with you"),
        (r"\bcalendar invite\b", 4, "calendar invite"),
        (r"\bvirtual (?:onsite|on-site)\b", 4, "virtual onsite"),
        (r"\bonsite\b", 3, "onsite"),
        (r"\bhiring manager\b", 3, "hiring manager"),
    ],
    "applied": [
        (
            r"başvurunuz .+? şirketine gönderildi|başvurunuzu aldık|başvurunuz(?:u)? (?:bize )?ulaştı|başvurunuz görüntülendi",
            6,
            "application confirmation (Turkish)",
        ),
        (
            r"\byou (?:have )?applied\b|\bapplication (?:was |has been )?sent to\b",
            6,
            "application sent",
        ),
        (
            r"\bthank(?:s| you)? (?:for|again for) (?:your )?appl\w+",
            6,
            "thanks for applying",
        ),
        (
            r"\bappl\w+ (?:has been |was |is )?(?:received|submitted|complete[d]?)\b",
            6,
            "application received",
        ),
        (r"\bwe(?:'ve| have) received your appl\w+", 6, "we received your application"),
        (
            r"\breceived your (?:application|resume|cv)\b",
            5,
            "received your application",
        ),
        (r"\byour application (?:to|for|at|with|has been)\b", 3, "your application"),
        (
            r"\bapplication (?:received|submitted|confirmed|confirmation)\b",
            6,
            "application confirmation",
        ),
        (r"\bthanks for your interest\b", 4, "thanks for your interest"),
        (r"\bthank you for your interest\b", 4, "thank you for your interest"),
        (
            r"\byour (?:candidacy|profile) (?:has been|is|was) (?:received|submitted|registered)\b",
            5,
            "candidacy received",
        ),
        (r"\bsuccessfully (?:applied|submitted)\b", 6, "successfully applied"),
        (r"\bwe(?:'ve| have) got your application\b", 6, "got your application"),
        (
            r"\breviewing your (?:application|candidacy)\b",
            4,
            "reviewing your application",
        ),
    ],
    "outreach": [
        (
            r"\bcame across your (?:profile|resume|cv|background)\b",
            5,
            "came across your profile",
        ),
        (r"\breaching out (?:about|regarding|to you)\b", 4, "reaching out"),
        (
            r"\bwould you be (?:open|interested|available)\b",
            4,
            "would you be interested",
        ),
        (
            r"\b(?:exciting|great|new) (?:opportunity|role|opening) (?:at|with)\b",
            3,
            "opportunity at",
        ),
        (r"\bopen (?:role|position|opportunity) (?:at|with)\b", 3, "open role at"),
        (r"\b(?:i am|i'm) a recruiter\b", 5, "i am a recruiter"),
        (r"\btalent (?:acquisition|partner|sourcer)\b", 3, "talent acquisition"),
        (r"\bperfect fit for (?:a|the|your)\b", 3, "perfect fit"),
        (r"\bjoin (?:our|the) team\b", 3, "join our team"),
        (r"\bare you (?:looking|open)\b", 3, "are you looking"),
    ],
}

# Order matters only as a tie-breaker: earlier wins on equal score.
KIND_PRIORITY = ["offer", "rejected", "assessment", "interview", "applied", "outreach"]

MIN_SCORE = 4

# A message has to look job-related before we even bother scoring it.
# "appl" needs its whole family spelled out: "applic\w+" alone misses "apply",
# "applying" and "applied", which are exactly what a short confirmation says.
RELEVANCE_KEYWORDS = re.compile(
    r"\b(?:appl(?:y|ies|ying|ied|icat\w*|icant\w*)|candidat\w+|interview\w*|recruit\w+|"
    r"hiring|resume|cv|position|role|job|opening|opportunit\w+|assess\w+|screening|"
    r"onboarding|talent|career\w*|offer|employ\w+|internship|vacanc\w+|başvuru\w*|mülakat\w*)\b",
    re.I,
)

# Strong signals that this is commerce or account mail, not hiring.
IRRELEVANT_KEYWORDS = re.compile(
    r"\b(?:your order|order confirmation|invoice|receipt|shipping confirmation|"
    r"tracking number|password reset|reset your password|subscription (?:renewed|renewal)|"
    r"payment received|statement is ready)\b",
    re.I,
)

ATS_DOMAINS = {
    "greenhouse.io",
    "greenhouse-mail.io",
    "lever.co",
    "hire.lever.co",
    "myworkday.com",
    "myworkdayjobs.com",
    "workday.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "bamboohr.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "successfactors.eu",
    "jobvite.com",
    "workable.com",
    "workablemail.com",
    "recruitee.com",
    "breezy.hr",
    "jazzhr.com",
    "jazz.co",
    "paylocity.com",
    "ultipro.com",
    "adp.com",
    "applytojob.com",
    "ziprecruiter.com",
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "hackerrank.com",
    "codility.com",
    "codesignal.com",
    "hirevue.com",
    "dayforcehcm.com",
    "phenompeople.com",
    "eightfold.ai",
    "paradox.ai",
    "teamtailor.com",
    "teamtailor-mail.com",
    "gupy.io",
    "workatastartup.com",
    "wellfound.com",
    "angel.co",
    "rippling.com",
    "paycom.com",
    "jobylon.com",
    "manatal.com",
}

# Display names that belong to the platform, not the employer. ATS senders often
# put the *employer* in the display name ("Trellis Hiring Team"), so we cannot
# simply ignore names from ATS domains — only these specific ones.
PLATFORM_NAMES = {
    "greenhouse",
    "greenhouse mail",
    "greenhouse.io",
    "lever",
    "lever mail",
    "workday",
    "myworkday",
    "myworkdayjobs",
    "ashby",
    "ashby hq",
    "smartrecruiters",
    "smart recruiters",
    "workable",
    "workable mail",
    "jobvite",
    "taleo",
    "icims",
    "bamboohr",
    "bamboo hr",
    "hackerrank",
    "hacker rank",
    "codility",
    "codesignal",
    "rippling",
    "successfactors",
    "sap successfactors",
    "teamtailor",
    "jobylon",
    "manatal",
    "sap",
    "linkedin",
    "indeed",
    "glassdoor",
    "wellfound",
    "wellfound (formerly angellist)",
    "ziprecruiter",
    "hirevue",
    "eightfold",
    "phenom",
    "phenom people",
    "paradox",
    "recruitee",
    "breezy",
    "breezy hr",
    "jobylon",
    "gupy",
    "paylocity",
    "ultipro",
    "paycom",
    "icims inc",
}

FREEMAIL_DOMAINS = {
    "gmail.com",
    "googlemail.com",
    "yahoo.com",
    "outlook.com",
    "hotmail.com",
    "live.com",
    "icloud.com",
    "me.com",
    "aol.com",
    "protonmail.com",
    "proton.me",
    "pm.me",
    "gmx.com",
    "fastmail.com",
    "hey.com",
    "msn.com",
    "yandex.com",
    "zoho.com",
}

# Words that show up in a company slot but are never a company.
COMPANY_STOPWORDS = {
    "your application",
    "application",
    "the team",
    "our team",
    "the company",
    "our company",
    "us",
    "you",
    "your team",
    "careers",
    "recruiting",
    "recruitment",
    "talent",
    "hr",
    "the role",
    "this role",
    "your candidacy",
    "candidacy",
    "the position",
    "the team at",
    "our careers",
    "your interest",
    "interest",
    "the application",
    "the opportunity",
    "our",
    "the",
    "we",
    "a",
    "an",
    "my",
    "your",
    "this",
    "that",
    "review",
    "candidates",
}

# Subdomains that say nothing about the company.
GENERIC_SUBDOMAINS = {
    "careers",
    "career",
    "jobs",
    "job",
    "mail",
    "email",
    "email",
    "em",
    "mg",
    "e",
    "apply",
    "hiring",
    "talent",
    "recruiting",
    "recruitment",
    "notifications",
    "notification",
    "no-reply",
    "noreply",
    "donotreply",
    "do-not-reply",
    "hello",
    "info",
    "news",
    "team",
    "my",
    "work",
    "www",
    "hr",
    "recruit",
    "smtp",
    "m",
    "go",
    "link",
    "links",
    "emails",
}

ROLE_KEYWORDS = re.compile(
    r"\b(?:engineer|engineering|developer|programmer|manager|management|designer|design|"
    r"analyst|analytics|scientist|intern|internship|lead|director|architect|consultant|"
    r"specialist|administrator|coordinator|researcher|marketer|marketing|writer|recruiter|"
    r"officer|associate|assistant|technician|representative|sales|account|executive|"
    r"software|hardware|data|product|program|project|frontend|front-end|backend|back-end|"
    r"full[- ]stack|devops|sre|qa|quality|security|cloud|platform|mobile|ios|android|"
    r"machine learning|ml|ai|operations|finance|accounting|legal|customer|support|"
    r"success|growth|content|brand|ux|ui|research|strategy|business|yazılım|geliştirici|"
    r"geliştirme|mühendis\w*|uzman\w*|stajyer\w*|yönetici\w*|analist)\b",
    re.I,
)

_TITLE_ROLE = re.compile(
    r"\b(?:for|regarding|about)\s+the\s+(.{2,60}?)\s+(?:role|position|opening|opportunity)\b",
    re.I,
)
_THE_ROLE = re.compile(
    r"\bthe\s+(.{2,60}?)\s+(?:role|position|opening|opportunity)\b", re.I
)
_TRAILING_ROLE = re.compile(r"\b(.{2,60}?)\s+(?:role|position|opening)\b", re.I)
_APPLYING_FOR = re.compile(
    r"\b(?:appl\w+|interview\w*|candidac\w+)\s+for\s+(?:the\s+)?(.{2,60}?)(?:\s+(?:role|position|opening)\b|[.!?,]|$)",
    re.I,
)
_FOR_THE = re.compile(r"\bfor\s+the\s+(.{2,60}?)(?:\s+at\b|[.!?,]|$)", re.I)

# Subject/display-name company extraction. Capitalised so we pick proper nouns.
_SUBJECT_COMPANY = [
    re.compile(p)
    for p in (
        r"\b(?i:applying to) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s+(?i:for|as)\b|[.!?,;:!]|\s*[-–|]|$)",
        r"\b(?i:application) (?i:to|at|with) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s+(?i:for|as)\b|[.!?,;:!]|\s*[-–|]|$)",
        r"\b(?i:your application) (?i:to|at|with) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s+(?i:for|as)\b|[.!?,;:!]|\s*[-–|]|$)",
        r"\b(?i:application for) .+?\s+(?i:at) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s+(?i:for|as)\b|[.!?,;:!]|\s*[-–|]|$)",
        r"\b(?i:interview|offer) (?i:with|at|from) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s+(?i:for|as)\b|[.!?,;:!]|\s*[-–|]|$)",
        r"\b(?i:assessment|test|challenge|interview|screening|exercise) (?i:for) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:[.!?,;:!]|\s*[-–|]|$)",
        r"\b(?i:opportunity|role|position|opening|vacancy|job) (?i:at|with) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:[.!?,;:!]|\s*[-–|]|$)",
        r"\b(?i:your) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,3}?) (?i:application)\b",
        r"\b(?i:your) ([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,3}?) (?i:journey)\b",
        r"^([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)\s+(?:(?i:is)\s+)?(?i:hiring|recruiting|careers)",
    )
]

_SUBJECT_COMPANY_LOOSE = [
    re.compile(p, re.I)
    for p in (
        r"\bapplying to ([a-z0-9][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s+(?:for|as)\b|[.!?,;:!]|\s*[-–|]|$)",
        r"\bapplication (?:to|at|with) ([a-z0-9][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s+(?:for|as)\b|[.!?,;:!]|\s*[-–|]|$)",
        r"\byour application (?:to|at|with) ([a-z0-9][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s+(?:for|as)\b|[.!?,;:!]|\s*[-–|]|$)",
    )
]

_NAME_VIA = re.compile(
    r"^(.+?)\s+via\s+(?:greenhouse|lever|workday|ashby|smartrecruiters|jobvite|workable)$",
    re.I,
)

# Trailing phrases that mark a display name as "someone at the company".
# Stripped repeatedly, so "Trellis Hiring Team" -> "Trellis".
_GENERIC_NAME_SUFFIX = re.compile(
    r"\s+(?:hiring\s+team|recruitment\s+team|recruiting\s+team|recruitment|recruiting|"
    r"talent\s+acquisition(?:\s+team)?|talent\s+team|people\s+team|talent|careers?|hiring|"
    r"no[\s-]*reply|do\s*not\s*reply|global\s+support|support|"
    r"job\s+board|jobs|notifications?|team|mail)\s*$",
    re.I,
)

# "Dana Whitfield - Norvale", "Jordan Reyes | Meridian-Networks": the employer is last.
_NAME_SEPARATOR = re.compile(r"\s+[|–—-]\s+")

# Phrases that mean we captured a sentence about an application, not a company.
_FLOW_WORDS = re.compile(
    r"\b(?:application|applications|applied|applying|received|submitted|confirmation|"
    r"confirm|update|thanks|thank|your|journey)\b",
    re.I,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" \t\r\n-–|,.:;\"'")


def _strip_generic_suffix(name: str) -> str:
    text = name
    for _ in range(3):
        stripped = _GENERIC_NAME_SUFFIX.sub("", text).strip()
        if stripped == text:
            break
        text = stripped
    # Concatenated forms the space-delimited rule misses: "SolisHR".
    concatenated = re.match(r"^(.{3,}?)\s*HR$", text, re.I)
    if concatenated:
        text = concatenated.group(1).strip()
    return text


def _company_candidate(value: str) -> str | None:
    """Clean a raw match and decide whether it can be an employer name."""
    cleaned = _clean(value)
    # "your application to join Trellis" -> "Trellis"
    cleaned = re.sub(
        r"^(?:join|at|the company|company)\s+", "", cleaned, flags=re.I
    ).strip()
    if not cleaned or cleaned.lower() in PLATFORM_NAMES:
        return None
    if not is_plausible_company(cleaned):
        return None
    return cleaned


def is_plausible_company(value: str) -> bool:
    if not value:
        return False
    cleaned = _clean(value)
    lowered = cleaned.lower()
    if len(cleaned) < 2 or len(cleaned) > 60:
        return False
    if lowered in COMPANY_STOPWORDS:
        return False
    if re.match(r"^(?:hybrid|remote|onsite|on-site)\b", lowered):
        return False
    if lowered.startswith(("your ", "our ", "the ", "a ", "an ")) and lowered not in {
        "the walt disney company",
    }:
        # "your application" -> reject, but allow names that legitimately start
        # with an article only when they are clearly a proper noun of 2+ words.
        if len(cleaned.split()) < 3:
            return False
    if lowered in {w.strip() for w in COMPANY_STOPWORDS}:
        return False
    # Reject if it is just a generic role word.
    if ROLE_KEYWORDS.fullmatch(cleaned):
        return False
    # A role *phrase* like "Full Stack Software Engineer" is not an employer.
    # Company names rarely end in a role word, which makes the last word a
    # useful discriminator ("Data Systems" survives, "Backend Engineer" does not).
    words = cleaned.split()
    if words and ROLE_KEYWORDS.fullmatch(words[-1]):
        # A proper name plus a business suffix is still an employer: Halcyon's
        # AI, Acme Software. Generic role phrases remain excluded.
        business_suffix = words[-1].lower() in {
            "ai",
            "software",
            "cloud",
            "analytics",
            "data",
            "research",
            "design",
        }
        named_company = len(words) > 1 and any(
            not ROLE_KEYWORDS.fullmatch(w) for w in words[:-1]
        )
        if not (business_suffix and named_company):
            return False
    # "SiteGround has been received" is a sentence fragment, not an employer.
    if _FLOW_WORDS.search(cleaned) and len(cleaned.split()) <= 5:
        return False
    return True


def _domain_of(sender_email: str) -> str:
    if "@" not in sender_email:
        return ""
    return sender_email.rsplit("@", 1)[1].lower().strip(">")


def _root_domain(domain: str) -> str:
    parts = [p for p in domain.split(".") if p]
    if len(parts) <= 2:
        return domain
    # strip generic subdomains from the left
    while len(parts) > 2 and parts[0] in GENERIC_SUBDOMAINS:
        parts.pop(0)
    return ".".join(parts[-2:])


# Public suffixes with a second level: "aday@kalemci.com.tr" has its
# employer in the third-to-last label, not the second-to-last.
MULTI_PART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "me.uk",
    "com.tr", "edu.tr", "gov.tr", "org.tr", "net.tr", "bel.tr", "pol.tr",
    "com.br", "com.ar", "com.mx", "com.co", "com.ve", "com.pe", "com.uy",
    "com.au", "com.cn", "com.hk", "com.sg", "com.my", "com.ph", "com.vn",
    "co.jp", "co.kr", "co.in", "co.il", "co.nz", "co.za", "co.id",
    "com.ua", "com.pl", "com.ro", "com.es", "com.pt", "com.gr", "com.cy",
    "com.eg", "com.sa", "com.qa", "com.kw", "com.bh", "com.jo", "com.lb",
}


def _employer_domain(domain: str) -> str:
    """The registrable domain of an employer address, suffix-aware.

    Differs from :func:`_root_domain` only for multi-part suffixes, which is
    where the inbound path has no use for them.
    """
    domain = (domain or "").strip().lower()
    parts = [p for p in domain.split(".") if p]
    if len(parts) < 3:
        return _root_domain(domain)
    if ".".join(parts[-2:]) in MULTI_PART_SUFFIXES:
        while len(parts) > 3 and parts[0] in GENERIC_SUBDOMAINS:
            parts.pop(0)
        return ".".join(parts[-3:])
    return _root_domain(domain)


def _company_from_domain(domain: str) -> str | None:
    return _company_from_label(_root_domain(domain))


def _company_from_label(root: str) -> str | None:
    if not root or root in ATS_DOMAINS or root in FREEMAIL_DOMAINS:
        return None
    base = root.split(".")[0]
    if base in GENERIC_SUBDOMAINS or len(base) < 3:
        return None
    if base.isdigit():
        return None
    return base.replace("-", " ").title()


def extract_company(msg: EmailMessage) -> str | None:
    subject = msg.subject or ""
    name = msg.sender_name or ""
    is_ats = _root_domain(_domain_of(msg.sender_email)) in ATS_DOMAINS

    if _root_domain(_domain_of(msg.sender_email)) == "linkedin.com":
        for pattern in (
            r"başvurunuz\s+(.+?)\s+şirketine gönderildi",
            r"^(.+?)\s+şirketindeki\s+.+?\s+başvurunuz",
            r"başvurunuz\s+(.+?)\s+tarafından görüntülendi",
        ):
            match = re.search(pattern, subject, re.I)
            if match:
                candidate = _company_candidate(match.group(1))
                if candidate:
                    return candidate
        # LinkedIn delivery templates name the actual employer, often in the body.
        for text in (subject, msg.snippet, msg.body[:6000]):
            match = re.search(
                r"\bapplication (?:was |has been )?sent to\s+([^\n.!]+)", text, re.I
            )
            if match:
                candidate = _company_candidate(match.group(1))
                if candidate:
                    return candidate

    # 1. The subject is the most reliable place, and its proper nouns are
    #    capitalised, so these patterns stay case-sensitive on the capture.
    for pattern in _SUBJECT_COMPANY:
        match = pattern.search(subject)
        if match:
            candidate = _company_candidate(match.group(1))
            if candidate:
                return candidate

    # A title can end in its employer, e.g. "Backend Engineer at Vantage".
    tail = re.search(
        r"\b(?:at|with)\s+([A-Z][\w&.'’\-]*(?:\s+[\w&.'’\-]+){0,4}?)(?:\s*[-–|]|[.!?,;:]|$)",
        subject,
    )
    if tail:
        candidate = _company_candidate(tail.group(1))
        if candidate:
            return candidate

    # 2. Separator layouts: "Application Received - Software Engineer - Acme Corp"
    #    and "Nordvale - Thank you for your application". Checked before the sender
    #    display name, which is often a person ("Riley Okonkwo - Cobalt").
    segmented = _company_from_segments(subject)
    if segmented:
        return segmented

    if name:
        # 3. "Acme via Greenhouse"
        match = _NAME_VIA.search(name)
        if match:
            candidate = _company_candidate(match.group(1))
            if candidate:
                return candidate

        # 4. "Dana Whitfield - Norvale" — the employer is the trailing part.
        if _NAME_SEPARATOR.search(name):
            candidate = _company_candidate(_NAME_SEPARATOR.split(name)[-1])
            if candidate:
                return candidate

        # 5. "Trellis Hiring Team" -> "Trellis". Runs even for ATS senders,
        #    because they usually name the employer, but PLATFORM_NAMES stops
        #    "Greenhouse Mail" from being mistaken for one.
        employer_name = re.sub(
            r"\s+@\s*(?:icims|greenhouse|lever|workday)\b.*$", "", name, flags=re.I
        )
        candidate = _company_candidate(_strip_generic_suffix(employer_name))
        if candidate:
            return candidate

    # 6. Lowercase subjects, strongest patterns only.
    for pattern in _SUBJECT_COMPANY_LOOSE:
        match = pattern.search(subject)
        if match:
            candidate = _company_candidate(match.group(1))
            if candidate and len(candidate.split()) <= 3:
                return candidate

    # 7. The sending domain — meaningless for an ATS relaying someone else.
    if not is_ats:
        domain_company = _company_from_domain(_domain_of(msg.sender_email))
        if domain_company:
            return domain_company

    return None


def _company_from_segments(subject: str) -> str | None:
    """Handle the separator layouts, e.g.
    'Application Received - Software Engineer - Acme Corp' and
    'Nordvale - Thank you for your application'."""
    for separator in (" - ", " – ", " | ", " — "):
        if separator not in subject:
            continue
        segments = [s.strip() for s in subject.split(separator) if s.strip()]
        if len(segments) < 2:
            continue

        # A leading short proper noun followed by a sentence is the employer.
        head = segments[0]
        if (
            len(head.split()) <= 4
            and head[0].isupper()
            and not ROLE_KEYWORDS.search(head)
            and not _FLOW_WORDS.search(head)
        ):
            candidate = _company_candidate(head)
            if candidate:
                return candidate

        # Otherwise the employer is the last descriptive segment.
        for segment in reversed(segments):
            if ROLE_KEYWORDS.search(segment):
                continue
            candidate = _company_candidate(segment)
            if candidate:
                return candidate
    return None


def _role_candidate(value: str) -> str | None:
    text = html.unescape(value).strip(" \t\n\r\"'‘’“”.,!?;:")
    # Remove sentence scaffolding, preserving punctuation inside actual titles.
    prefix = re.compile(
        r"^(?:(?:the|our|a|an)\s+|(?:position|role|job)\s*(?:of|as|:)?\s+|"
        r"(?:your\s+)?interest\s+in\s+|(?:thank(?: you|s)?\s+)?for\s+|"
        r"feedback\s+on\s+your\s+|in\s+our\s+)",
        re.I,
    )
    for _ in range(6):
        cleaned = prefix.sub("", text).strip(" \t\"'‘’“”")
        if cleaned == text:
            break
        text = cleaned
    text = re.split(r"\s+at\s+", text, maxsplit=1, flags=re.I)[0]
    text = re.sub(
        r"\s+(?:(?:job|position|role)\s+)?(?:was|has been|is)\b.*$",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(
        r"\s+(?:application|role|position|job|opening)$", "", text, flags=re.I
    )
    text = text.strip(" \t\"'‘’“”.,!?;:")
    if not text or len(text) > 120 or not ROLE_KEYWORDS.search(text):
        return None
    if re.search(
        r"\b(?:thank you|thanks for|received your|we have|we are|your interest|free|connects needed)\b",
        text,
        re.I,
    ):
        return None
    return text


_ROLE_PATTERNS = [
    re.compile(pattern, re.I)
    for pattern in (
        r"""\b(?:position|role|job)\s*:?\s*["“‘](.{2,120}?)["”’]""",
        r"\b(?:appl\w+|interview\w*|candidac\w+)\s+(?:for|to)\s*:?\s*(.{2,140}?)(?:\s+(?:role|position|opening)\b|\s+at\b|[!?]|\.(?=\s|$)|$)",
        r"\b(?:position|role)\s+(?:of|as)\s+(.{2,140}?)(?:\s+at\b|[!?]|\.(?=\s|$)|$)",
        r"\b(?:for|regarding|about)\s+the\s+(.{2,140}?)\s+(?:role|position|opening|opportunity)\b",
        r"\b(?:the|our)\s+(.{2,140}?)\s+(?:role|position|opening)\b",
        r"\bfeedback on your\s+(.{2,140}?)\s+application\b",
    )
]


def upwork_invitation_role(msg: EmailMessage) -> str | None:
    """Upwork supplies an explicit job name, even when it has no role keywords."""
    if _root_domain(_domain_of(msg.sender_email)) != "upwork.com":
        return None
    for text in (msg.subject, msg.body[:6000], msg.snippet):
        match = re.search(
            r"^[ \t]*(?:#{1,6}[ \t]*)?Invitation[ \t]+to[ \t]+Interview[ \t]+for[ \t]*:[ \t]*([^\r\n]+)",
            text,
            re.I | re.M,
        )
        if match and (role := match.group(1).strip()):
            return role
    return None


def extract_role(msg: EmailMessage) -> str | None:
    invitation_role = upwork_invitation_role(msg)
    if invitation_role:
        return invitation_role
    employer = extract_company(msg)

    def title(value: str) -> str | None:
        candidate = _role_candidate(value)
        return (
            candidate
            if candidate and candidate.casefold() != (employer or "").casefold()
            else None
        )

    if _root_domain(_domain_of(msg.sender_email)) == "linkedin.com":
        explicit = re.search(r"şirketindeki\s+(.+?)\s+başvurunuz", msg.subject, re.I)
        if explicit:
            candidate = title(explicit.group(1))
            if candidate:
                return candidate
        lines = [
            line.strip()
            for line in msg.body.splitlines()
            if line.strip() and not re.search(r"https?://|@", line)
        ]
        heading = re.compile(
            r"başvurunuz .+? şirketine gönderildi|application (?:was |has been )?sent to|başvurunuz(?: .+? tarafından)? görüntülendi",
            re.I,
        )

        def employer_line(value: str) -> bool:
            label = value.split(" · ", 1)[0].strip().casefold()
            return bool(employer) and label == employer.casefold()

        for index, line in enumerate(lines[:20]):
            if heading.search(line):
                # The applied-job card precedes recommended jobs and profile footers.
                following_lines = lines[index + 1 :]
                while following_lines and heading.search(following_lines[0]):
                    following_lines = following_lines[1:]
                for following in following_lines[:3]:
                    if employer_line(following):
                        break
                    candidate = title(following)
                    if candidate:
                        return candidate
                card = following_lines[:2]
                if len(card) == 2 and employer_line(card[1]):
                    if 2 <= len(card[0]) <= 120:
                        return card[0]
                if lines[index + 1 :]:
                    return None
                break

    if _root_domain(_domain_of(msg.sender_email)) == "linkedin.com" and re.search(
        r"application (?:was |has been )?sent|you (?:have )?applied|başvurunuz .+? şirketine gönderildi|başvurunuz görüntülendi",
        msg.subject + "\n" + msg.body,
        re.I,
    ):
        # LinkedIn often renders the title on its own line, rather than in a sentence.
        titles = {
            candidate
            for line in msg.body.splitlines()[:40]
            if (candidate := title(line.strip())) and not _FLOW_WORDS.search(candidate)
        }
        if len(titles) == 1:
            return titles.pop()

    # Explicit title patterns first; body text can supply a title missing from
    # the subject or truncated Gmail snippet. Sentence fragments aren't titles.
    for raw in (msg.subject or "", msg.snippet or "", msg.body[:6000] or ""):
        text = html.unescape(raw)
        for pattern in _ROLE_PATTERNS:
            for match in pattern.finditer(text):
                candidate = title(match.group(1))
                if candidate:
                    return candidate
        if raw == (msg.subject or ""):
            for separator in (" - ", " – ", " | ", " — "):
                for segment in text.split(separator) if separator in text else []:
                    candidate = title(segment)
                    if candidate and not _FLOW_WORDS.search(candidate):
                        return candidate
    return None


RELEVANCE_JOB = "job"
RELEVANCE_UNKNOWN = "unknown"
RELEVANCE_IRRELEVANT = "irrelevant"


def relevance(msg: EmailMessage) -> str:
    """How sure are the rules that this message is about a job application?

    ``irrelevant`` is a confident no (commerce or account mail), ``job`` is a
    confident yes, and ``unknown`` means no job vocabulary was found at all —
    which is precisely the case worth escalating to a language model.
    """
    subject = msg.subject or ""

    if IRRELEVANT_KEYWORDS.search(subject) and not RELEVANCE_KEYWORDS.search(subject):
        return RELEVANCE_IRRELEVANT

    if _root_domain(_domain_of(msg.sender_email)) in ATS_DOMAINS:
        return RELEVANCE_JOB

    haystack = " ".join(filter(None, [subject, msg.snippet, msg.body[:4000]]))
    return RELEVANCE_JOB if RELEVANCE_KEYWORDS.search(haystack) else RELEVANCE_UNKNOWN


def is_job_related(msg: EmailMessage) -> bool:
    return relevance(msg) == RELEVANCE_JOB


# Words in a Subject line that say the *sender* was applying, rather than
# reporting on someone else's application.
OUTBOUND_SUBJECT = re.compile(
    r"\b(?:appl\w+|candidac\w+|apply|referral|resume|cv|cover\s+letter)\b|"
    r"\biş\s+başvuru\w*\b|\bbaşvuru\w*\b|\bözgeçmiş\b|\bmülakat\w*\b",
    re.I,
)


def outbound_application(msg: EmailMessage) -> Classification | None:
    """Classify the mailbox's own mail as an application the user sent.

    Returns ``None`` when the message is not an outbound application, or when
    the recipient tells us nothing about the employer (a personal contact, a
    CV sent to a friend, or mail forwarded to oneself).

    Identity comes from the recipient rather than the sender, because the
    sender is the user. The subject patterns and the domain-based employer
    guess are shared with the inbound classifier by temporarily pointing
    ``sender`` at the recipient.
    """
    if not OUTBOUND_SUBJECT.search(msg.subject or ""):
        return None

    recipient = msg.to_email
    if not recipient:
        return None
    recipient_domain = _root_domain(_domain_of(recipient))
    if not recipient_domain or recipient_domain in FREEMAIL_DOMAINS:
        # A gmail contact is a person, not a company.
        return None

    company = _recipient_company(recipient, msg)
    if not company:
        return None

    # Reuse the inbound extraction rules with the employer in the sender slot.
    proxy = EmailMessage(
        message_id=msg.message_id,
        thread_id=msg.thread_id,
        subject=msg.subject,
        sender=recipient,
        date=msg.date,
        snippet=msg.snippet,
        body=msg.body,
    )
    role = extract_role(proxy)

    return Classification(
        kind="applied",
        score=6,
        company=company,
        role=role,
        matched=["outbound application"],
    )


def _recipient_company(recipient: str, msg: EmailMessage):
    """Best guess at the employer behind the address the user wrote to.

    Subject first, then the recipient's domain, because the sender slot of an
    outbound message is the mailbox owner and carries no employer.
    """
    domain = _domain_of(recipient)
    root = _root_domain(domain)
    employer_domain = _employer_domain(domain)

    # "novastudio@jobs.workablemail.com": the ATS puts the employer in the
    # local part, not the domain.
    if root in ATS_DOMAINS:
        for candidate in recipient.split("@")[0].replace("+", ".").split("."):
            name = _company_candidate(candidate.replace("-", " "))
            if name and is_plausible_company(name):
                return name.title()
        return None

    for pattern in _SUBJECT_COMPANY:
        match = pattern.search(msg.subject or "")
        if match:
            candidate = _company_candidate(match.group(1))
            if candidate and is_plausible_company(candidate):
                return candidate

    for label in (employer_domain, root):
        name = _company_from_label(label)
        # "someone@abc.ie" would read "Abc": too short to be a real label,
        # so the application goes to the ignored cache instead.
        if name and len(name.split(".")[0]) >= 4:
            return name
    return None


def _score(
    msg: EmailMessage, text: str
) -> tuple[dict[str, float], dict[str, list[str]]]:
    scores: dict[str, float] = {}
    matched: dict[str, list[str]] = {}

    for kind, signals in SIGNALS.items():
        total = 0.0
        hits: list[str] = []
        for pattern, weight, label in signals:
            regex = re.compile(pattern, re.I)
            subject_hit = bool(regex.search(msg.subject or ""))
            body_hit = bool(regex.search(text))
            if subject_hit:
                total += weight * 2
                hits.append(label)
            elif body_hit:
                total += weight
                hits.append(label)
        if total:
            scores[kind] = total
            matched[kind] = hits

    return scores, matched


def classify(msg: EmailMessage) -> Classification | None:
    """Classify an email, or return ``None`` if it is not a job-application email."""
    if not is_job_related(msg):
        return None

    text = " ".join(filter(None, [msg.subject, msg.snippet, msg.body[:20000]]))
    scores, matched = _score(msg, text)
    if not scores:
        return None

    best = max(
        scores.items(),
        key=lambda item: (item[1], -KIND_PRIORITY.index(item[0])),
    )
    kind, score = best
    if score < MIN_SCORE:
        return None

    return Classification(
        kind=kind,
        score=score,
        company=extract_company(msg),
        role=extract_role(msg),
        matched=matched.get(kind, []),
    )
