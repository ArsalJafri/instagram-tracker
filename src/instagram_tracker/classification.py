"""Two-axis classification: what kind of work, and how it is employed.

Replaces the phrase-literal rules that preceded it. Those compared fixed phrases against
prose that does not agree on word order, so SpaceX's *New Graduate Engineer, Software*
was filed as "no software signal" despite containing both words — the fifth widening in
four days, and the one that made the primitive itself the problem.

Evidence here is matched as **token groups in any order**. A signal is a list of groups;
it fires when every group finds one of its alternatives somewhere in the text. So
``[{software}, {engineer, developer, ...}]`` matches "software engineer", "engineer,
software" and "software development" alike, with a smaller vocabulary than the phrase
lists it replaces.

Role and employment are scored independently and only the pair decides routing, which
removes the exclusive-or that used to conflate "not software" with "no level stated".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .jobs import JobDetails
from .models import (
    ClassificationResult,
    ClassificationSource,
    Destination,
    EmploymentClass,
    InputQuality,
    RoleClass,
)

# Bump whenever vocabulary, weights or thresholds change. Stored on every run so a later
# version can be replayed over the same observations and compared.
CLASSIFIER_VERSION = "scorer-2026-08-14"

# Weight at which a label is considered fully evidenced. Reaching it with one strong
# signal or several weak ones is deliberately equivalent.
SATURATION = 3.0

# Fetch sources that yield a real description. The rest give a title and little else.
_RICH_SOURCES = {"json-ld", "rendered"}

# A bounded set of inflections, so "engineer" matches "engineering" and "intern" matches
# "internship". Deliberately not open-ended: unrestricted suffixes would make "intern"
# match "internal", turning an Internal Tools role into an internship.
_INFLECTIONS = r"(?:s|es|ing|ed|ship)?"

Group = tuple[str, ...]


@dataclass(frozen=True)
class Signal:
    """Evidence for one label. Fires when every group matches, in any order.

    ``title_only`` is the important knob. Order-free matching is precise over a title —
    six words, all of them about the role — and far too loose over a 4000-character
    description, where any two words co-occur eventually. So role-defining signals and
    every negative read the title, and only weak corroborating evidence (a language, a
    degree subject, "return to school") is allowed to read the body.
    """

    name: str
    weight: float
    groups: tuple[Group, ...]
    title_only: bool = False


def _sig(name: str, weight: float, *groups: Group, title_only: bool = False) -> Signal:
    return Signal(name=name, weight=weight, groups=tuple(groups), title_only=title_only)


# The word that makes a role a *building* role, in any order relative to its qualifier.
_BUILDS = ("engineer", "developer", "development", "programmer")

ROLE_SIGNALS: dict[RoleClass, list[Signal]] = {
    RoleClass.SOFTWARE: [
        # The SpaceX fix: order-free, so "Engineer, Software" scores like "Software Engineer".
        _sig("software+builds", 3.0, ("software",), _BUILDS, title_only=True),
        _sig("swe", 3.0, ("swe",), title_only=True),
        _sig("backend", 2.5, ("backend", "back end"), _BUILDS, title_only=True),
        _sig("frontend", 2.5, ("frontend", "front end"), _BUILDS, title_only=True),
        _sig("fullstack", 2.5, ("fullstack", "full stack"), _BUILDS, title_only=True),
        _sig("web", 2.5, ("web",), _BUILDS, title_only=True),
        _sig("mobile", 2.5, ("mobile", "ios", "android"), _BUILDS, title_only=True),
        _sig("application", 2.5, ("application",), _BUILDS, title_only=True),
        _sig("embedded", 2.5, ("embedded",), _BUILDS, title_only=True),
        _sig("forward-deployed", 2.5, ("forward deployed",), _BUILDS, title_only=True),
        _sig("site-reliability", 2.5, ("site reliability",), title_only=True),
        _sig("sre", 2.0, ("sre",), title_only=True),
        _sig("devops", 2.5, ("devops",), title_only=True),
        _sig("platform", 2.0, ("platform",), _BUILDS, title_only=True),
        # Weaker: "systems engineer" is a real software title and also a mechanical one.
        _sig("systems", 1.5, ("systems",), _BUILDS, title_only=True),
        # An ML engineer builds software. Analyst-flavoured data work scores as DATA.
        _sig("ml-engineer", 3.0, ("machine learning", "deep learning"), _BUILDS, title_only=True),
        _sig("data-engineer", 3.0, ("data",), ("engineer", "engineering"), title_only=True),
        # Degree subject, so genuinely useful from a description.
        _sig("computer-science", 1.5, ("computer science",)),
        # Individually weak stack signals. Several together carry a title that says
        # nothing on its own — "Technology Summer Analyst" is the motivating case.
        _sig("lang-java", 0.5, ("java",)),
        _sig("lang-python", 0.5, ("python",)),
        _sig("lang-cpp", 0.5, ("c++",)),
        _sig("lang-js", 0.5, ("javascript", "typescript")),
        _sig("lang-go", 0.5, ("golang",)),
        _sig("framework-react", 0.5, ("react", "angular", "vue")),
        _sig("apis", 0.5, ("api",)),
        _sig("distributed", 0.75, ("distributed systems",)),
        _sig("databases", 0.5, ("database", "sql")),
        _sig("cloud", 0.5, ("aws", "azure", "kubernetes", "docker")),
        _sig("version-control", 0.5, ("git",)),
        _sig("algorithms", 0.5, ("algorithms", "data structures")),
        # Disqualifiers. "Engineer" alone must never reach a software channel.
        _sig("not-mechanical", -3.0, ("mechanical",), title_only=True),
        _sig("not-civil", -3.0, ("civil",), title_only=True),
        _sig("not-chemical", -3.0, ("chemical",), title_only=True),
        _sig("not-biomedical", -3.0, ("biomedical", "biological"), title_only=True),
        _sig("not-structural", -3.0, ("structural",), title_only=True),
        _sig("not-electrical", -2.5, ("electrical",), title_only=True),
        _sig("not-industrial", -2.5, ("industrial",), title_only=True),
        _sig("not-manufacturing", -2.0, ("manufacturing",), title_only=True),
    ],
    RoleClass.DATA: [
        _sig("data-scientist", 3.0, ("data",), ("scientist", "science"), title_only=True),
        _sig("data-analyst", 3.0, ("data",), ("analyst", "analytics"), title_only=True),
        _sig("business-intelligence", 2.5, ("business intelligence",), title_only=True),
        _sig("machine-learning", 1.5, ("machine learning", "deep learning"), title_only=True),
        _sig("statistics", 1.0, ("statistics", "statistical"), title_only=True),
    ],
    RoleClass.QUANT: [
        _sig("quantitative", 3.0, ("quantitative", "quant"),
             ("researcher", "research", "trader", "trading", "analyst", "developer"),
             title_only=True),
        _sig("quant-bare", 2.0, ("quantitative",), title_only=True),
        _sig("derivatives", 1.0, ("derivatives", "market making"), title_only=True),
    ],
    RoleClass.IT: [
        _sig("help-desk", 3.0, ("help desk", "helpdesk", "service desk"), title_only=True),
        _sig("it-support", 3.0, ("it support", "desktop support", "technical support"),
             title_only=True),
        _sig("sysadmin", 2.5, ("system administrator", "sysadmin", "systems administrator"),
             title_only=True),
        _sig("network-admin", 2.5, ("network administrator", "network technician"),
             title_only=True),
    ],
    RoleClass.PRODUCT: [
        _sig("product-manager", 3.0, ("product",), ("manager", "management", "owner"),
             title_only=True),
    ],
}

EMPLOYMENT_SIGNALS: dict[EmploymentClass, list[Signal]] = {
    EmploymentClass.INTERN: [
        _sig("intern", 3.0, ("intern",), title_only=True),
        _sig("co-op", 3.0, ("co op", "coop"), title_only=True),
        _sig("summer-analyst", 2.5, ("summer",), ("analyst", "associate", "scholar"),
             title_only=True),
        # The description signals. These are why a title saying nothing can still be
        # classified — an employer describing the programme gives the answer away.
        _sig("return-to-school", 2.5, ("return to school", "returning to school")),
        _sig("currently-enrolled", 1.5, ("currently pursuing", "currently enrolled")),
        _sig("week-programme", 1.5, ("10 week", "12 week", "ten week", "twelve week")),
        _sig("rising", 2.0, ("rising senior", "rising junior")),
        _sig("undergraduate", 1.0, ("undergraduate",)),
    ],
    EmploymentClass.FULL_TIME: [
        # Read from the title: an internship description saying "full-time for the summer"
        # is the classic way to mislabel an internship as permanent.
        _sig("full-time", 2.5, ("full time",), title_only=True),
        _sig("permanent", 2.0, ("permanent",), title_only=True),
        # Contiguous phrases on purpose. As order-free groups, "New York" plus a stray
        # "graduate" anywhere in a description would read as a new-grad role.
        _sig("new-grad", 3.0, ("new grad", "new graduate", "new college graduate"),
             title_only=True),
        _sig("college-graduate", 3.0, ("college graduate", "university graduate"),
             title_only=True),
        _sig("entry-level", 2.5, ("entry level",), title_only=True),
        _sig("early-career", 2.5, ("early career",), title_only=True),
        _sig("numbered", 2.0, ("engineer i", "engineer ii", "engineer 1", "engineer 2",
                               "developer i", "developer ii", "developer 1", "developer 2"),
             title_only=True),
        _sig("grad-programme", 2.0, ("graduate development program", "rotational program",
                                     "graduate program"), title_only=True),
        _sig("campus-hire", 1.5, ("campus hire",)),
        # Seniority defeats the full-time rule, never the internship one: "Senior Software
        # Engineer (Full-Time)" must not reach the new-grad channel on the strength of
        # its employment type. "Product Manager Intern" trips `manager` while plainly
        # being an internship, so manager is scoped here where it cannot do that harm.
        _sig("not-senior", -3.0, ("senior", "staff", "principal", "lead"), title_only=True),
        _sig("not-management", -3.0, ("manager", "director", "head", "vp"), title_only=True),
    ],
    EmploymentClass.CONTRACT: [
        _sig("contract", 2.5, ("contract",), title_only=True),
        _sig("contractor", 3.0, ("contractor",), title_only=True),
        _sig("part-time", 2.5, ("part time",), title_only=True),
        _sig("temporary", 2.0, ("temporary", "seasonal"), title_only=True),
    ],
}


def normalize(text: str) -> str:
    """Lowercase and flatten separators, without destroying multi-word signals.

    Hyphens and underscores become spaces so "co-op", "full-time" and schema.org's
    `FULL_TIME` all reduce to the spellings the vocabulary is written in. Commas and
    slashes go too, which is what lets "Engineer, Software" read as adjacent words.
    """
    lowered = text.replace("–", "-").replace("—", "-").lower()
    flattened = re.sub(r"[-_,/|()\[\]:;]+", " ", lowered)
    return re.sub(r"\s+", " ", flattened).strip()


def _entry_matches(haystack: str, entry: str) -> bool:
    pattern = rf"(?<![a-z0-9]){re.escape(entry)}{_INFLECTIONS}(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


def _fires(signal: Signal, title: str, body: str) -> bool:
    haystack = title if signal.title_only else body
    return all(any(_entry_matches(haystack, e) for e in group) for group in signal.groups)


def _score(signals: dict, title: str, body: str) -> tuple[dict, list[str]]:
    """Total the weight of every signal that fires, per label."""
    totals = {label: 0.0 for label in signals}
    evidence: list[tuple[float, str]] = []
    for label, group in signals.items():
        for signal in group:
            if _fires(signal, title, body):
                totals[label] += signal.weight
                evidence.append((abs(signal.weight), f"{label.value}:{signal.name}"))
    evidence.sort(key=lambda pair: -pair[0])
    return totals, [name for _, name in evidence]


def _confidence(totals: dict) -> tuple[object, float]:
    """Pick the winning label and score how much to trust it.

    Two things must both hold for confidence to be high: enough evidence was found, and
    the winner is clear of the runner-up. A single weak signal and a two-way tie both
    score low, which is what sends genuinely ambiguous postings to review.

    This is a bounded score, not a calibrated probability. Thresholds are tuned against
    observed classifications, not derived.
    """
    ranked = sorted(totals.items(), key=lambda pair: -pair[1])
    top_label, top = ranked[0]
    if top <= 0:
        return None, 0.0
    runner_up = max(ranked[1][1], 0.0) if len(ranked) > 1 else 0.0

    evidence_factor = min(1.0, top / SATURATION)
    margin = (top - runner_up) / top
    return top_label, round(evidence_factor * (0.5 + 0.5 * margin), 3)


# -- rules ---------------------------------------------------------------
#
# Deliberately few. These exist only for evidence strong enough that asking the scorer
# would be theatre; anything arguable belongs in the vocabulary instead.


def _employment_rule(employment_field: str, body: str) -> tuple[EmploymentClass, str] | None:
    declared = normalize(employment_field)
    if declared:
        # The employer's own structured statement. schema.org spells these INTERN,
        # FULL_TIME, PART_TIME, CONTRACTOR, TEMPORARY.
        if _entry_matches(declared, "intern"):
            return EmploymentClass.INTERN, "structured-employment-intern"
        if _entry_matches(declared, "full time"):
            return EmploymentClass.FULL_TIME, "structured-employment-full-time"
        for token in ("contractor", "contract", "part time", "temporary"):
            if _entry_matches(declared, token):
                return EmploymentClass.CONTRACT, "structured-employment-contract"

    for phrase in ("return to school", "must return to university", "returning to school"):
        if _entry_matches(body, phrase):
            return EmploymentClass.INTERN, "return-to-school"
    return None


def classify_job(
    details: JobDetails,
    url: str,
    role_threshold: float = 0.60,
    employment_threshold: float = 0.55,
    poor_input_penalty: float = 0.15,
) -> ClassificationResult:
    """Score both axes and decide where the posting goes."""
    quality = (
        InputQuality.RICH if details.source in _RICH_SOURCES else InputQuality.POOR
    )
    title = normalize(details.title or "")
    body = normalize(f"{details.title or ''} {details.text}")

    role_totals, role_evidence = _score(ROLE_SIGNALS, title, body)
    role_label, role_confidence = _confidence(role_totals)
    role = role_label or RoleClass.OTHER

    employment_totals, employment_evidence = _score(EMPLOYMENT_SIGNALS, title, body)
    employment_label, employment_confidence = _confidence(employment_totals)
    employment = employment_label or EmploymentClass.UNKNOWN

    source = ClassificationSource.SCORER
    rule = None
    if (fired := _employment_rule(details.employment_type or "", body)) is not None:
        employment, rule = fired
        employment_confidence = 0.95
        source = ClassificationSource.RULE
        employment_evidence.insert(0, f"rule:{rule}")

    # A title-less posting was never read. Scoring its empty text would manufacture a
    # verdict about a page nobody saw, so it goes to review regardless of the numbers.
    if not details.title:
        role, role_confidence = RoleClass.OTHER, 0.0
        employment, employment_confidence = EmploymentClass.UNKNOWN, 0.0

    penalty = poor_input_penalty if quality is InputQuality.POOR else 0.0
    destination = _route(
        role,
        role_confidence,
        employment,
        employment_confidence,
        role_threshold + penalty,
        employment_threshold + penalty,
    )

    return ClassificationResult(
        role=role,
        role_confidence=role_confidence,
        employment=employment,
        employment_confidence=employment_confidence,
        destination=destination,
        source=source,
        classifier_version=CLASSIFIER_VERSION,
        input_quality=quality,
        evidence=(role_evidence + employment_evidence)[:12],
        rule=rule,
    )


def _route(
    role: RoleClass,
    role_confidence: float,
    employment: EmploymentClass,
    employment_confidence: float,
    role_threshold: float,
    employment_threshold: float,
) -> Destination:
    if role is not RoleClass.SOFTWARE or role_confidence < role_threshold:
        return Destination.REVIEW
    if employment_confidence < employment_threshold:
        return Destination.REVIEW
    if employment is EmploymentClass.INTERN:
        return Destination.INTERNSHIP
    if employment is EmploymentClass.FULL_TIME:
        return Destination.FULL_TIME
    return Destination.REVIEW
