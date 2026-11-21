"""Transparent risk scoring.

score = min(100, sum(weight[severity] x source_factor)) over all findings, where
  weight = critical 30, high 15, medium 7, low 3, informational 0
  source_factor = 1.0 for deterministic rules and AI findings whose quoted evidence was found in the document,
                  0.0 for AI findings whose evidence could not be verified (shown, but not scored).
Overall level:
  critical  - any critical finding or score >= 80
  high      - score >= 45 or at least two high findings
  medium    - score >= 15 or any high finding
  low       - otherwise
The score is a prioritisation aid for review. It is not a legal conclusion.
"""

from __future__ import annotations

from typing import Any

from app.services.rules import Finding

WEIGHTS = {"critical": 30, "high": 15, "medium": 7, "low": 3, "informational": 0}
METHOD = (
    "Score = sum of severity weights (critical 30, high 15, medium 7, low 3, informational 0) for deterministic "
    "rule findings and for AI findings whose quoted evidence was verified in the document; capped at 100. "
    "Unverified AI findings are shown but do not count. Level: critical if any critical finding or score >= 80; "
    "high if score >= 45 or two or more high findings; medium if score >= 15 or any high finding; otherwise low."
)


def compute_score(findings: list[Finding]) -> tuple[int, str, dict[str, Any]]:
    contributions = []
    total = 0.0
    counts = dict.fromkeys(WEIGHTS, 0)
    for f in findings:
        factor = 1.0 if (f.source == "rule" or f.verified) else 0.0
        points = WEIGHTS.get(f.severity, 0) * factor
        total += points
        counts[f.severity] = counts.get(f.severity, 0) + 1
        contributions.append({"title": f.title, "severity": f.severity, "source": f.source,
                              "verified": f.verified, "points": round(points, 1)})
    score = int(min(100, round(total)))
    if counts["critical"] or score >= 80:
        level = "critical"
    elif score >= 45 or counts["high"] >= 2:
        level = "high"
    elif score >= 15 or counts["high"]:
        level = "medium"
    else:
        level = "low"
    return score, level, {"method": METHOD, "weights": WEIGHTS, "counts": counts, "contributions": contributions,
                          "disclaimer": "This score prioritises review effort. It is not legal advice or a legal conclusion."}
