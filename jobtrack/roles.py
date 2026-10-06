"""Group free-text role titles into a small set of categories.

Titles arrive in many shapes: "Software Engineer", "Yazılım Mühendisi", or
"Yapay Zekâ Destekli Yazılım Geliştirici (AI-Native Developer)". The primary
path asks a language model to map each distinct title onto a shared category,
because that copes with wording and languages no rule list anticipates. Those
answers are cached per normalized title, so the model is consulted at most once
per new title.

The keyword matcher below is the offline fallback: it keeps the widget working
when no model is configured, the provider is unreachable, or the workspace runs
in rules-only mode. It also backstops any title the model declines to label.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import unicodedata
from collections import Counter
from pathlib import Path

UNSPECIFIED = "Role not stated"
OTHER = "Other"

# Turkish letters fold to their ASCII counterparts so "yazılım" and "yazilim"
# match the same keyword. Accented vowels (â in "zekâ") are handled by the
# combining-mark strip in _normalize.
_TR_FOLD = str.maketrans(
    {
        "ı": "i",
        "İ": "i",
        "ş": "s",
        "Ş": "s",
        "ğ": "g",
        "Ğ": "g",
        "ç": "c",
        "Ç": "c",
        "ö": "o",
        "Ö": "o",
        "ü": "u",
        "Ü": "u",
        "â": "a",
        "î": "i",
        "û": "u",
    }
)

# Ordered: the first category with a keyword hit wins. Stacks and disciplines
# come before the generic Software Development bucket.
ROLE_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Fullstack Development",
        ("full stack", "fullstack", "tam yigin", "tam yigin gelistirici"),
    ),
    (
        "Backend Development",
        (
            "backend",
            "back end",
            "server side",
            "server side developer",
            "api developer",
            "api engineer",
            "microservice",
            "java",
            "python developer",
            "python engineer",
            "node.js",
            "node js",
            "nodejs",
            "golang",
            "go developer",
            "rust developer",
            "ruby on rails",
            "rails developer",
            "django",
            "spring boot",
            "laravel",
            "php",
            ".net",
            "dotnet",
            "c# developer",
            "arka yuz",
            "arka yuz gelistirici",
        ),
    ),
    (
        "Frontend Development",
        (
            "frontend",
            "front end",
            "react",
            "react native",
            "vue",
            "angular",
            "svelte",
            "next.js",
            "ui developer",
            "ui engineer",
            "web developer",
            "web engineer",
            "javascript developer",
            "typescript developer",
            "css",
            "html",
            "on yuz",
            "on yuz gelistirici",
        ),
    ),
    (
        "Mobile Development",
        (
            "mobile",
            "mobil",
            "ios developer",
            "ios engineer",
            "android",
            "flutter",
            "swift",
            "kotlin",
            "xamarin",
            "mobil uygulama",
        ),
    ),
    (
        "DevOps & Cloud",
        (
            "devops",
            "dev ops",
            "sre",
            "site reliability",
            "platform engineer",
            "cloud engineer",
            "cloud architect",
            "cloud developer",
            "infrastructure",
            "infra engineer",
            "kubernetes",
            "terraform",
            "systems engineer",
            "system administrator",
            "sysadmin",
            "network engineer",
            "bulut",
        ),
    ),
    (
        "Data & Analytics",
        (
            "data engineer",
            "data analyst",
            "data architect",
            "data warehouse",
            "big data",
            "business intelligence",
            "bi developer",
            "bi analyst",
            "etl",
            "veri muhendisi",
            "veri analisti",
            "veri ambari",
        ),
    ),
    (
        "Quality Assurance",
        (
            "qa",
            "quality assurance",
            "test engineer",
            "test automation",
            "automation engineer",
            "sdet",
            "tester",
            "test muhendisi",
            "test otomasyon",
            "kalite",
        ),
    ),
    (
        "Security",
        (
            "security",
            "guvenlik",
            "cybersecurity",
            "cyber security",
            "siber",
            "penetration",
            "pentest",
            "infosec",
            "soc analyst",
        ),
    ),
    (
        "Embedded & Hardware",
        (
            "embedded",
            "gomulu",
            "firmware",
            "iot",
            "donanim",
            "hardware engineer",
        ),
    ),
    (
        "Game Development",
        (
            "game developer",
            "game engineer",
            "oyun gelistirici",
            "oyun programci",
            "unity developer",
            "unreal",
        ),
    ),
    (
        "AI & Machine Learning",
        (
            "machine learning",
            "makine ogrenmesi",
            # Only explicit AI-role phrases: "Yapay Zekâ Destekli Yazılım
            # Geliştirici" is a software role, so the bare adjective must not
            # pull it into this bucket.
            "yapay zeka muhendisi",
            "yapay zeka gelistirici",
            "yapay zeka uzmani",
            "yapay zeka arastirmaci",
            "artificial intelligence",
            "ai engineer",
            "ai developer",
            "ai researcher",
            "ai research",
            "ml engineer",
            "ml developer",
            "deep learning",
            "derin ogrenme",
            "nlp",
            "natural language",
            "computer vision",
            "bilgisayarli goru",
            "data scientist",
            "veri bilimci",
            "llm engineer",
            "generative ai",
            "large language model",
            "prompt engineer",
        ),
    ),
    (
        "Design",
        (
            "designer",
            "tasarimci",
            "ux",
            "ui ux",
            "user experience",
            "kullanici deneyimi",
            "product designer",
            "visual designer",
            "graphic designer",
            "grafik tasarim",
        ),
    ),
    (
        "Product & Project Management",
        (
            "product manager",
            "product owner",
            "project manager",
            "program manager",
            "scrum master",
            "urun yoneticisi",
            "urun sahibi",
            "proje yoneticisi",
            "technical program",
        ),
    ),
    (
        "Software Development",
        (
            "software",
            "yazilim",
            "developer",
            "gelistirici",
            "programmer",
            "programci",
            "swe",
            "coder",
            "software architect",
            "yazilim mimari",
        ),
    ),
    (
        "Marketing & Growth",
        (
            "marketing",
            "pazarlama",
            "growth",
            "seo",
            "content",
            "social media",
            "sosyal medya",
            "brand",
        ),
    ),
    (
        "Sales & Business",
        (
            "sales",
            "satis",
            "account executive",
            "business development",
            "business analyst",
            "is gelistirme",
        ),
    ),
    (
        "Customer Support",
        (
            "support",
            "destek",
            "customer success",
            "musteri",
            "call center",
            "cagri merkezi",
        ),
    ),
    (
        "Finance & Operations",
        (
            "finance",
            "finans",
            "accounting",
            "muhasebe",
            "operations",
            "operasyon",
            "logistics",
            "lojistik",
            "supply chain",
            "tedarik",
        ),
    ),
    (
        "People & Recruiting",
        (
            "human resources",
            "insan kaynaklari",
            "recruiter",
            "recruitment",
            "talent",
            "yetenek",
        ),
    ),
)

# The labels a model may return, derived from the fallback list so the two
# paths agree. UNSPECIFIED is not offered: empty titles never reach the model.
CATEGORIES: tuple[str, ...] = tuple(label for label, _ in ROLE_CATEGORIES) + (OTHER,)


def _normalize(role: str) -> str:
    """Lowercase, fold Turkish letters, strip accents, and pad with spaces.

    Padding lets keyword checks treat " java " and " yazilim " as whole tokens,
    so "java" does not match inside "javascript" and "ai" is only matched as
    part of an explicit phrase.
    """
    text = role.lower().translate(_TR_FOLD)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    return f" {text.strip()} "


def role_category(role: str | None) -> str:
    """Return the display category for one role title, without a model."""
    if not role or not role.strip():
        return UNSPECIFIED
    text = _normalize(role)
    for label, keywords in ROLE_CATEGORIES:
        if any(f" {keyword} " in text for keyword in keywords):
            return label
    return OTHER


# Bump when the prompt or category set changes, invalidating cached answers.
PROMPT_VERSION = 1
CACHE_FILENAME = "role_categories.json"
# Titles per model call. Kept small so the JSON reply stays well under the
# provider's max_tokens and never truncates into invalid JSON.
RESOLVE_BATCH = 20

ROLE_PROMPT = f"""\
Group job titles for a job-search dashboard. Titles come from recruiting emails
in English, Turkish, or a mix, and one job can be worded many ways
("Software Engineer", "Yazılım Mühendisi", "Yapay Zekâ Destekli Yazılım
Geliştirici (AI-Native Developer)"). Ignore seniority, location, employment type
and company; group by what the job actually is.

Allowed categories (return these labels exactly):
{chr(10).join(f"- {label}" for label in CATEGORIES)}

Rules:
- Similar titles, including across languages, share one category.
- A software job whose title merely mentions AI as a modifier is Software Development.
- "AI Engineer", "Machine Learning Engineer" and "Yapay Zeka Mühendisi" are AI & Machine Learning.
- Backend, frontend, fullstack and mobile are their own categories, not Software Development.
- Titles unrelated to tech (sales, marketing, support, finance, people) use those categories.
- If nothing fits, use Other.

Return ONLY a JSON object mapping each title exactly as given to one label, e.g.
{{"Kıdemli Veri Mühendisi": "Data & Analytics", "Full Stack Developer": "Fullstack Development"}}
"""


class RoleCategoryCache:
    """Normalized-title to label answers, persisted next to the settings file."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self) -> dict[str, str]:
        if self.path is None:
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict) or raw.get("version") != PROMPT_VERSION:
            return {}
        categories = raw.get("categories")
        return (
            {str(k): str(v) for k, v in categories.items()}
            if isinstance(categories, dict)
            else {}
        )

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def put(self, key: str, label: str) -> None:
        with self._lock:
            self._data[key] = label
            self._save()

    def _save(self) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": PROMPT_VERSION, "categories": self._data}
        descriptor, name = tempfile.mkstemp(
            prefix=".roles-", suffix=".json.tmp", dir=self.path.parent
        )
        tmp = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
            tmp.chmod(0o600)
            tmp.replace(self.path)
        finally:
            tmp.unlink(missing_ok=True)


class RoleCategorizer:
    """Map titles to categories with a model, falling back to keyword rules.

    Model answers are cached per normalized title, so steady-state overview
    requests never call the provider. A provider that keeps failing is disabled
    for the life of the object, mirroring the analyzer's degradation, so a dead
    endpoint is not retried on every request.
    """

    def __init__(self, llm=None, cache: RoleCategoryCache | None = None, max_failures: int = 2):
        self.llm = llm
        self.cache = cache if cache is not None else RoleCategoryCache()
        self.max_failures = max_failures
        self.failures = 0
        self.disabled = False

    def map(self, roles) -> dict[str, str]:
        """Return {original title: category} for every distinct non-empty title."""
        mapping: dict[str, str] = {}
        pending: list[tuple[str, str]] = []
        for role in roles:
            if not role or not role.strip():
                continue
            key = _normalize(role).strip()
            cached = self.cache.get(key)
            if cached is None:
                pending.append((key, role))
            else:
                mapping[role] = cached

        # Chunk the request: providers cap the reply (max_tokens), and a JSON
        # object covering every title at once gets truncated into invalid JSON
        # for larger workspaces. Small batches stay well inside the limit.
        for start in range(0, len(pending), RESOLVE_BATCH):
            chunk = pending[start : start + RESOLVE_BATCH]
            resolved: dict[str, str] = {}
            asked = False
            if self.llm is not None and not self.disabled:
                try:
                    resolved = self._ask([role for _, role in chunk])
                    asked = True
                    self.failures = 0
                except Exception:
                    self.failures += 1
                    self.disabled = self.failures >= self.max_failures
            for key, role in chunk:
                label = resolved.get(key)
                if label:
                    self.cache.put(key, label)
                    mapping[role] = label
                elif asked:
                    # The model answered but skipped this title; keep it on rules
                    # and cache that so the next request is not another round trip.
                    fallback = role_category(role)
                    self.cache.put(key, fallback)
                    mapping[role] = fallback
                else:
                    # A failed call must not be cached: the title should reach
                    # the model again once the provider recovers.
                    mapping[role] = role_category(role)
        return mapping

    def _ask(self, titles: list[str]) -> dict[str, str]:
        from jobtrack.llm import extract_json

        listing = "\n".join(f"- {title}" for title in titles)
        payload = extract_json(self.llm.complete(ROLE_PROMPT, listing))
        allowed = {label.lower(): label for label in CATEGORIES}
        answer: dict[str, str] = {}
        for title, label in payload.items():
            if not isinstance(label, str):
                continue
            canonical = allowed.get(label.strip().lower())
            if canonical:
                answer[_normalize(str(title)).strip()] = canonical
        return answer


def build_categorizer() -> RoleCategorizer:
    """Model-backed categorizer when a provider is configured, else rules only."""
    from jobtrack.config import DATA_DIR
    from jobtrack.settings import load as load_settings

    cache = RoleCategoryCache(DATA_DIR / CACHE_FILENAME)
    mode = (
        load_settings().llm_mode
        or os.environ.get("JOBTRACK_LLM_MODE")
        or "auto"
    )
    if mode == "off":
        return RoleCategorizer(None, cache)

    from jobtrack.llm import LLMConfig, build_llm

    try:
        config = LLMConfig.load()
    except Exception:
        return RoleCategorizer(None, cache)
    # Role titles are tiny; a short timeout keeps the first overview snappy when
    # a configured provider happens to be unreachable.
    config.timeout = min(config.timeout or 20.0, 20.0)
    return RoleCategorizer(build_llm(config), cache)


def summarize_roles(applications, categorizer: RoleCategorizer | None = None) -> list[dict]:
    """Count applications per role category, most common first."""
    if categorizer is None:
        counts = Counter(role_category(a.role) for a in applications)
    else:
        labels = categorizer.map([a.role for a in applications])
        counts = Counter(
            labels.get(a.role) or role_category(a.role) for a in applications
        )
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]
