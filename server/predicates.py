"""Predicate aliases: the same relation under different snake_case names.

The LLM extractor is asked for one canonical predicate per kind of fact, but it
still writes ``date_of_birth`` on one edition and ``birth_date`` on another. The
alignment predicate guard (alignment.py) and the deterministic verdict layer
(verdicts.py) compare ``canonical(predicate)`` instead of the raw string, so those
claims are paired and graded.

Only true synonyms belong here: two names for one relation. Related but different
relations (``position_start`` vs ``employment_start_date``) stay apart, because the
deterministic layer compares their values directly.
"""

_GROUPS = {
    "birth_date": ["date_of_birth", "born", "born_on", "birthdate", "dob"],
    "death_date": ["date_of_death", "died", "died_on", "deathdate", "dod"],
    "birth_place": ["place_of_birth", "born_in", "birthplace"],
    "death_place": ["place_of_death", "died_in", "deathplace"],
    "burial_place": ["place_of_burial", "buried_in", "buried_at"],
    "spouse": ["married_to", "spouses", "husband", "wife"],
    "marriage_date": ["date_of_marriage", "married_on", "wedding_date"],
    "educated_at": ["education", "alma_mater", "studied_at"],
    "occupation": ["occupations", "profession", "occupation_and_status",
                   "occupations_and_titles"],
    "member_of": ["membership", "memberships", "founding_member_of"],
    "office_held": ["held_office", "position_held", "held_ministerial_office"],
    "named_after": ["namesake", "eponym"],
    "collection_destroyed": ["collections_destroyed"],
    "founded": ["founding_date", "foundation_date", "date_founded", "inception"],
    "dissolved": ["dissolution_date", "date_dissolved"],
    "population": ["population_total", "inhabitants"],
    "area_km2": ["area", "total_area_km2"],
}

ALIAS = {}
for canon, names in _GROUPS.items():
    ALIAS[canon] = canon
    for n in names:
        ALIAS[n] = canon


def canonical(predicate) -> str:
    """Lower-cased canonical predicate ('' for none)."""
    p = str(predicate or "").strip().lower()
    return ALIAS.get(p, p)


def aliases(predicate) -> list:
    """Every stored spelling that means the same relation, canonical first."""
    canon = canonical(predicate)
    if not canon:
        return []
    return [canon] + [n for n in _GROUPS.get(canon, []) if n != canon]


def same(a, b) -> bool:
    """Both predicates present and naming the same relation."""
    ca, cb = canonical(a), canonical(b)
    return bool(ca) and ca == cb


# Facts about the page's own subject, which rules extraction names in each language's
# own spelling ("Snorri Sturluson" / "Snorre Sturlasson"): no subject check for these.
PAGE_SUBJECT_PREDICATES = {"birth_date", "death_date", "birth_place", "death_place"}


def _tokens(s) -> set:
    import re
    return {t for t in re.findall(r"\w+", str(s or "").lower()) if len(t) >= 3 and not t.isdigit()}


def same_subject(predicate, subject_a, subject_b) -> bool:
    """Could two claims with this predicate be about the same thing? A generic predicate
    (announcement_date, start_date, founded) says nothing on its own: "Blue Reform
    founded 2017" and "the cabinet founded 2015" are different facts. Subjects are the
    LLM's canonical English names, compared by shared words. Unknown subjects pass."""
    if canonical(predicate) in PAGE_SUBJECT_PREDICATES:
        return True
    ta, tb = _tokens(subject_a), _tokens(subject_b)
    if not ta or not tb:
        return True
    return len(ta & tb) / max(len(ta), len(tb)) >= 0.5
