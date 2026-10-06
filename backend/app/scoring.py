"""Scoring engine.

A solve is worth:

    score = base_points + efficiency_bonus - hints_cost     (never below 0)

* base_points - the points configured for the level in the event (or the
  challenge default in practice). Defaults escalate steeply with level number
  (see challenges/definitions.py) so clearing a harder level is always worth
  more than grinding an easier one.
* efficiency_bonus - rewards skill: solving with fewer attempts and fewer
  model tokens. The bonus pool is half the base points and decays by
  ATTEMPT_COST for every attempt after the first and by one point per
  TOKENS_PER_POINT model tokens spent on the level. It never goes negative:
  a long, expensive solve still earns the full base points.

Attempts and tokens are measured across EVERY conversation the player has had
on that level in that scope, so a reset or new chat does not launder effort.
Both figures are frozen into the solve row at solve time; the leaderboard
additionally shows live effort totals.

All scoring happens server-side from the authoritative turns/solves tables;
nothing in a client payload is trusted.

A solved level can also earn POSTMORTEM_POINTS once, by getting the Level
Postmortem's defensive-decision question right (engine: app/postmortem.py).
That award is recorded separately, in `postmortems`, and added to the
leaderboard total; it is never folded into a solve row, so the efficiency
bonus and the hint deductions stay exactly as they were.
"""

from __future__ import annotations

BONUS_FRACTION = 0.5     # bonus pool as a fraction of base points
POSTMORTEM_POINTS = 200  # flat award for the Level Postmortem decision
ATTEMPT_COST = 20        # bonus lost per attempt after the first
TOKENS_PER_POINT = 400   # model tokens that cost one point of bonus


def efficiency_bonus(base_points: int, attempts: int, tokens: int) -> int:
    """Bonus awarded at solve time. Deterministic, monotonic: more attempts or
    more tokens never increases it; it is clamped to [0, pool]."""
    pool = int(base_points * BONUS_FRACTION)
    decay = ATTEMPT_COST * max(0, attempts - 1) + max(0, tokens) // TOKENS_PER_POINT
    return max(0, pool - decay)


def net_score(points: int, bonus: int, hints_cost: int) -> int:
    """Final score for one solve."""
    return max(0, points + bonus - hints_cost)
