"""Shared data contracts for paper-verify.

These dataclasses are the schema every other module agrees on:

    extract  -> Citation
    fetch    -> Fetched   (keyed to a Citation by .id)
    judge    -> Verdict   (keyed to a Citation by .id)
    score    -> ScoredCitation, Report
    report   -> renders a Report to Markdown

Keeping the contract in one place means the modules are independent: any of
them can be replaced as long as it honours these shapes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class Verdict(str, Enum):
    """How well a cited claim matches the fetched source."""

    MATCH = "Match"            # claim is explicitly supported by the source
    PARTIAL = "Partial"        # partially supported; numbers / year / nuance differ
    MISMATCH = "Mismatch"      # absent from, or contradicted by, the source
    UNCERTAIN = "Uncertain"    # source insufficient to decide — flag for human review
    INACCESSIBLE = "Inaccessible"  # paywall / 404 / timeout — could not verify

    @property
    def symbol(self) -> str:
        return {
            "Match": "✅",
            "Partial": "⚠️",
            "Mismatch": "❌",
            "Uncertain": "❓",
            "Inaccessible": "⚫",
        }[self.value]


class Tier(str, Enum):
    """Overall grade for a single citation (or document average)."""

    A = "A"  # 90-100  citable in a thesis / formal report
    B = "B"  # 70-89   fine for lecture / blog, minor fixes
    C = "C"  # 50-69   must be re-checked
    F = "F"  # 0-49    do not cite; replace the source

    @property
    def symbol(self) -> str:
        return {"A": "🟢", "B": "🟡", "C": "🟠", "F": "🔴"}[self.value]

    @staticmethod
    def from_score(score: float) -> "Tier":
        if score >= 90:
            return Tier.A
        if score >= 70:
            return Tier.B
        if score >= 50:
            return Tier.C
        return Tier.F


@dataclass
class Citation:
    """One extracted reference plus the surrounding sentence (context)."""

    id: int
    type: str          # "URL" | "DOI" | "PMC" | "PMID" | "arXiv"
    ref: str           # the raw reference string
    context: str       # ~100 chars before/after, the claim being made
    line: int = 0      # 1-based line number in the source document

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Citation":
        return Citation(
            id=int(d["id"]),
            type=d["type"],
            ref=d["ref"],
            context=d.get("context", ""),
            line=int(d.get("line", 0)),
        )


@dataclass
class Fetched:
    """Result of fetching a citation's source."""

    id: int
    status: int = 0            # HTTP status (0 = never reached)
    title: str = ""
    abstract: str = ""         # abstract / main extracted text (may be truncated)
    url_final: str = ""        # final URL after redirects
    via_archive: bool = False  # True if served from web.archive.org fallback
    error: str = ""            # populated on failure
    authors: list = field(default_factory=list)  # structured author names (metadata APIs)
    year: Optional[int] = None  # publication year (metadata APIs), else None
    source: str = ""           # which path produced the data: crossref|arxiv|ncbi|http|archive|none
    soft_404_suspect: bool = False  # 2xx body looks like an error/placeholder page
    landing_status: Optional[int] = None  # real HTTP status of the landing URL on a metadata hit (None = not probed)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and not self.error

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Fetched":
        return Fetched(
            id=int(d["id"]),
            status=int(d.get("status", 0)),
            title=d.get("title", ""),
            abstract=d.get("abstract", ""),
            url_final=d.get("url_final", ""),
            via_archive=bool(d.get("via_archive", False)),
            error=d.get("error", ""),
            authors=list(d.get("authors", []) or []),
            year=(int(d["year"]) if d.get("year") is not None else None),
            source=d.get("source", ""),
            soft_404_suspect=bool(d.get("soft_404_suspect", False)),
            landing_status=(int(d["landing_status"]) if d.get("landing_status") is not None else None),
        )


@dataclass
class Judgement:
    """One judge's verdict on one citation."""

    judge: str          # judge name, e.g. "anthropic:claude-sonnet-4-6"
    verdict: Verdict
    reason: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


@dataclass
class ScoredCitation:
    """A citation with its fetch result, judgements, and computed score."""

    citation: Citation
    fetched: Optional[Fetched]
    judgements: list = field(default_factory=list)  # list[Judgement]
    score: float = 0.0
    breakdown: dict = field(default_factory=dict)   # rubric item -> points
    # The verdict actually used for the claim-match score (set by score_citation).
    # Differs from judgements[0] when judges split (-> Uncertain) or a tie-break
    # resolves disagreement; the display must reflect this, not the first judge.
    effective_verdict: Optional[Verdict] = None

    @property
    def consensus(self) -> Optional[Verdict]:
        """The verdict used for scoring (effective consensus).

        Falls back to the first judge's verdict only when no effective verdict
        was recorded (e.g. a ScoredCitation built outside score_citation).
        """
        if self.effective_verdict is not None:
            return self.effective_verdict
        if not self.judgements:
            return None
        return self.judgements[0].verdict

    @property
    def cross_check_agree(self) -> Optional[bool]:
        """Whether all judges returned the same verdict (None if <2 judges)."""
        if len(self.judgements) < 2:
            return None
        verdicts = {j.verdict for j in self.judgements}
        return len(verdicts) == 1

    @property
    def tier(self) -> Tier:
        return Tier.from_score(self.score)

    def to_dict(self) -> dict:
        consensus = self.consensus
        return {
            "citation": self.citation.to_dict(),
            "fetched": self.fetched.to_dict() if self.fetched else None,
            "judgements": [j.to_dict() for j in self.judgements],
            "consensus": consensus.value if consensus else None,
            "effective_verdict": consensus.value if consensus else None,
            "score": self.score,
            "breakdown": self.breakdown,
            "tier": self.tier.value,
        }


@dataclass
class Report:
    """Full document verification result."""

    source_file: str
    level: str                       # "L1" | "L2" | "L3"
    scored: list = field(default_factory=list)  # list[ScoredCitation]
    judges: list = field(default_factory=list)   # judge names used
    profile: Optional[str] = None    # active harness profile key, or None

    @property
    def overall_score(self) -> float:
        if not self.scored:
            return 0.0
        return round(sum(s.score for s in self.scored) / len(self.scored), 1)

    @property
    def overall_tier(self) -> Tier:
        return Tier.from_score(self.overall_score)

    @property
    def has_failure(self) -> bool:
        """True if any citation is tier F — flags the whole document."""
        return any(s.tier is Tier.F for s in self.scored)

    def tier_distribution(self) -> dict:
        dist = {t: 0 for t in Tier}
        for s in self.scored:
            dist[s.tier] += 1
        return dist

    def to_jsonl(self) -> str:
        return "\n".join(
            json.dumps(s.to_dict(), ensure_ascii=False) for s in self.scored
        )
