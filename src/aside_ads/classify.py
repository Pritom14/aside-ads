"""Keyword + embedding classifiers for sensitive contexts and topic categories."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from aside_ads.categories import SENSITIVE, TOPICS, Category
from aside_ads.matching import ContextAnalysis, Matcher
from aside_ads.text import any_phrase


@dataclass(frozen=True)
class Hit:
    category: str
    via: str  # term | pattern | semantic
    score: float = 1.0


class CategoryClassifier:
    def __init__(self, categories: Iterable[Category], matcher: Matcher, threshold: float) -> None:
        self.categories = list(categories)
        self.matcher = matcher
        self.threshold = threshold
        self._patterns = {c.name: [re.compile(p, re.IGNORECASE) for p in c.patterns] for c in self.categories}

    def classify(self, ctx: ContextAnalysis, *, first_only: bool = False) -> list[Hit]:
        hits: list[Hit] = []
        lowered = ctx.text.lower().replace("’", "'")
        remaining = []
        for cat in self.categories:
            if any_phrase(ctx.phrase_text, list(cat.terms)):
                hits.append(Hit(cat.name, "term"))
            elif any(p.search(lowered) for p in self._patterns[cat.name]):
                hits.append(Hit(cat.name, "pattern"))
            else:
                remaining.append(cat)
                continue
            if first_only:
                return hits
        for cat in remaining:
            if not cat.exemplars:
                continue
            sim = self.matcher.exemplar_similarity(ctx, cat.exemplars)
            if sim is None:
                break  # backend has no semantic similarity (BM25)
            if sim >= self.threshold:
                hits.append(Hit(cat.name, "semantic", sim))
                if first_only:
                    return hits
        return hits


def load_extra_categories(path: Path | None) -> dict[str, Category]:
    """Load extra categories from JSON.

    Format: ``{"category": ["term", ...]}`` or
    ``{"category": {"terms": [...], "patterns": [...], "exemplars": [...]}}``.
    """
    if not path:
        return {}
    raw = json.loads(Path(path).read_text())
    out: dict[str, Category] = {}
    for name, spec in raw.items():
        if isinstance(spec, list):
            spec = {"terms": spec}
        out[name] = Category(
            name,
            terms=tuple(spec.get("terms", [])),
            patterns=tuple(spec.get("patterns", [])),
            exemplars=tuple(spec.get("exemplars", [])),
        )
    return out


def build_sensitive_guard(
    enabled: list[str], matcher: Matcher, threshold: float, extra_file: Path | None = None
) -> CategoryClassifier:
    """Sensitive categories = enabled defaults, merged with (and extended by) the optional terms file."""
    unknown = [n for n in enabled if n not in SENSITIVE]
    if unknown:
        raise ValueError(f"Unknown sensitive categories: {unknown}. Known: {sorted(SENSITIVE)}")
    cats = {n: SENSITIVE[n] for n in enabled}
    for name, extra in load_extra_categories(extra_file).items():
        base = cats.get(name) or SENSITIVE.get(name) or Category(name)
        cats[name] = Category(
            name,
            terms=base.terms + extra.terms,
            patterns=base.patterns + extra.patterns,
            exemplars=base.exemplars + extra.exemplars,
            description=base.description,
        )
    return CategoryClassifier(cats.values(), matcher, threshold)


def build_topic_classifier(matcher: Matcher, threshold: float) -> CategoryClassifier:
    return CategoryClassifier(TOPICS.values(), matcher, threshold)
