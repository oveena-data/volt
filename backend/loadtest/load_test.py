"""VOLT load test: N concurrent active players against a running backend.

An "active player" registers, opens a level, then loops: send a message,
read the reply, think for a few seconds. Simultaneous inference requests are
therefore a fraction of active players (bounded further by the server's
shared inference semaphore) — the report states both.

Usage:
    python -m loadtest.load_test --base-url http://127.0.0.1:8099 \
        --players 25 --duration 90 --think-min 5 --think-max 12

Honest numbers only: this measures the full stack it is pointed at. Point it
at production-like infrastructure (real DB, real inference latency) for
capacity planning; the committed results in docs/LOADTEST.md state exactly
what was measured.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
import uuid

import httpx

BENIGN = [
    "Hello! What can you help with?",
    "Tell me about the substation panel.",
    "What does 'feeder' mean?",
    "How is the load looking today?",
    "Give me a quick status summary.",
]


class Stats:
    def __init__(self):
        self.turn_latencies: list[float] = []
        self.api_latencies: list[float] = []
        self.errors: dict[str, int] = {}
        self.turns_ok = 0
        self.rate_limited = 0
        self.queue_full = 0
        self.inflight = 0
        self.max_inflight = 0

    def err(self, kind: str):
        self.errors[kind] = self.errors.get(kind, 0) + 1


async def player(i: int, base: str, stats: Stats, stop_at: float,
                 think: tuple[float, float], challenge: str):
    async with httpx.AsyncClient(base_url=base, timeout=180) as c:
        try:
            t0 = time.monotonic()
            r = await c.post("/api/auth/register", json={
                "email": f"load{uuid.uuid4().hex[:12]}@example.com",
                "password": "loadtest-password-1", "display_name": f"Load {i}"})
            r.raise_for_status()
            stats.api_latencies.append(time.monotonic() - t0)
            tok = r.json()["token"]
            h = {"Authorization": f"Bearer {tok}"}
            t0 = time.monotonic()
            r = await c.post("/api/game/sessions", headers=h, json={
                "challenge_id": challenge, "mode": "practice"})
            r.raise_for_status()
            stats.api_latencies.append(time.monotonic() - t0)
            gsid = r.json()["game_session_id"]
        except Exception as e:
            stats.err(f"setup:{type(e).__name__}")
            return

        while time.monotonic() < stop_at:
            text = random.choice(BENIGN)
            msg_id = uuid.uuid4().hex
            t0 = time.monotonic()
            stats.inflight += 1
            stats.max_inflight = max(stats.max_inflight, stats.inflight)
            try:
                r = await c.post(f"/api/game/sessions/{gsid}/message",
                                 headers=h,
                                 json={"client_msg_id": msg_id, "text": text})
            except Exception as e:
                stats.err(f"transport:{type(e).__name__}")
                stats.inflight -= 1
                continue
            finally:
                pass
            dt = time.monotonic() - t0
            stats.inflight -= 1
            if r.status_code == 200:
                stats.turns_ok += 1
                stats.turn_latencies.append(dt)
            elif r.status_code == 429:
                code = r.json().get("error", {}).get("code")
                if code == "queue_full":
                    stats.queue_full += 1
                else:
                    stats.rate_limited += 1
                await asyncio.sleep(2)
            else:
                stats.err(f"http:{r.status_code}")
            await asyncio.sleep(random.uniform(*think))


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:8099")
    ap.add_argument("--players", type=int, default=25)
    ap.add_argument("--duration", type=float, default=90)
    ap.add_argument("--think-min", type=float, default=5)
    ap.add_argument("--think-max", type=float, default=12)
    ap.add_argument("--challenge", default="l1")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    stats = Stats()
    stop_at = time.monotonic() + args.duration
    t_start = time.time()
    await asyncio.gather(*(
        player(i, args.base_url, stats, stop_at,
               (args.think_min, args.think_max), args.challenge)
        for i in range(args.players)))

    report = {
        "players": args.players,
        "duration_s": args.duration,
        "think_time_s": [args.think_min, args.think_max],
        "turns_ok": stats.turns_ok,
        "throughput_turns_per_s": round(stats.turns_ok / args.duration, 2),
        "turn_latency_s": {
            "p50": round(pct(stats.turn_latencies, 0.50) or 0, 3),
            "p95": round(pct(stats.turn_latencies, 0.95) or 0, 3),
            "p99": round(pct(stats.turn_latencies, 0.99) or 0, 3),
            "max": round(max(stats.turn_latencies), 3) if stats.turn_latencies else None,
            "mean": round(statistics.fmean(stats.turn_latencies), 3)
                    if stats.turn_latencies else None,
        },
        "max_simultaneous_turn_requests": stats.max_inflight,
        "rate_limited_429": stats.rate_limited,
        "queue_full_429": stats.queue_full,
        "errors": stats.errors,
        "started_at": t_start,
    }
    print(json.dumps(report, indent=2))
    if args.out:
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
