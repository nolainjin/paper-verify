"""paper-verify — citation fact-checking for documents.

Extract citations (URL / DOI / PMC / arXiv) from a Markdown or text file,
fetch each source, judge whether the cited claim actually matches the source
using one or more LLMs (independent cross-check), score the document on a
100-point rubric, and emit a Markdown report.

The core has **no required third-party dependencies** and no dependency on any
agent-orchestration framework. LLM providers are optional extras.
"""

from .models import Citation, Fetched, Verdict, Tier, ScoredCitation, Report

__all__ = [
    "Citation",
    "Fetched",
    "Verdict",
    "Tier",
    "ScoredCitation",
    "Report",
    "__version__",
]

__version__ = "0.1.0"
