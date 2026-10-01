#!/usr/bin/env python3
"""player_value.py - NBA player value in ONE command (stdlib only, Python 3.9+).

    python3 player_value.py --name "Jalen Duren" --season 2025-26 --vorp 3.5 --prior-vorp 2.7

Prints, in order:
  1. Production value: what the season was worth at that season's cap.
  2. Market estimate: expected AAV on a new deal starting the next season
     (two-season VORP blend x market discount, priced at the next season's cap).
  3. Deal check (only if --deal is given): four cap-share bases for a real salary
     schedule, and whether its AAV sits inside the market range.

Other modes of the same command:  --csv FILE (many players)  |  --selftest

MODEL
  VORP   = (BPM + 2.0) * MP / (82 * 48)                    [Basketball-Reference]
  WAR    = 2.7 * VORP                                       [BBRef]
  $/win  = (payroll_ratio - 15 * min_ratio) * cap / 27      [27 = league WAR per team]
  value  = min_ratio * cap + WAR * $/win                    [replacement = minimum]
  blend  = 0.6 * last-season VORP + 0.4 * prior-season VORP
  market AAV % of cap = production %(blend) * discount (0.64 to 0.74)
  Discount fit on 2 rookie-scale extensions (Sengun 0.636, Okongwu 0.738, AAV basis).
  Thin sample: refit on 15+ deals before trusting the range.
"""
from __future__ import annotations

import argparse
import csv
import math
import sys

CAP_BY_SEASON = {  # key = year the season ends. Spotrac cap history / NBA PR.
    2016: 70_000_000, 2017: 94_143_000, 2018: 99_093_000, 2019: 101_869_000,
    2020: 109_140_000, 2021: 109_140_000, 2022: 112_414_000, 2023: 123_655_000,
    2024: 136_021_000, 2025: 140_588_000, 2026: 154_647_000, 2027: 164_961_000,
}
DEFAULT_GROWTH = 0.055
REPLACEMENT_BPM = -2.0
VORP_TO_WAR = 2.7
LEAGUE_WAR_PER_TEAM = -REPLACEMENT_BPM * 5 * VORP_TO_WAR  # 27
PAYROLL_RATIO = 193_900_000 / 154_647_000   # 2025-26 measured, ~1.254
MIN_RATIO = 2_296_274 / 154_647_000         # 2025-26 two-year minimum, ~1.485% of cap
BLEND = (0.6, 0.4)                          # last season, prior season
DISCOUNT = (0.64, 0.74)                     # market AAV % / production %, AAV basis
MAX_TIERS = (0.25, 0.30, 0.35)


# ---------- core math ----------
def season_key(s) -> int:
    """'2025-26', '2026', 2026 -> 2026 (year the season ends)."""
    s = str(s).strip()
    if "-" in s:
        return int(s.split("-")[0]) + 1
    return int(s)


def season_label(k: int) -> str:
    return f"{k - 1}-{str(k)[-2:]}"


def cap_for(k: int, growth: float = DEFAULT_GROWTH) -> tuple[float, bool]:
    """Cap for season-end year k. Returns (cap, projected?)."""
    if k in CAP_BY_SEASON:
        return float(CAP_BY_SEASON[k]), False
    last = max(CAP_BY_SEASON)
    if k < min(CAP_BY_SEASON):
        raise ValueError(f"No cap on file for {season_label(k)}; pass --cap")
    return CAP_BY_SEASON[last] * (1 + growth) ** (k - last), True


def to_vorp(vorp=None, bpm=None, mp=None):
    if vorp is not None:
        return float(vorp)
    if bpm is not None and mp is not None:
        return (float(bpm) - REPLACEMENT_BPM) * float(mp) / (82 * 48)
    return None


def season_value(cap, vorp, payroll_ratio=PAYROLL_RATIO, min_ratio=MIN_RATIO) -> dict:
    war = vorp * VORP_TO_WAR
    dpw = (payroll_ratio - 15 * min_ratio) * cap / LEAGUE_WAR_PER_TEAM
    value = min_ratio * cap + war * dpw
    return {"vorp": vorp, "war": war, "dpw": dpw, "value": value, "pct": value / cap, "cap": cap}


def market_estimate(vorp_last, vorp_prior, next_cap, discount=DISCOUNT, **kw) -> dict:
    blend = vorp_last if vorp_prior is None else BLEND[0] * vorp_last + BLEND[1] * vorp_prior
    prod_pct = season_value(next_cap, blend, **kw)["pct"]  # % of cap is cap-invariant
    lo, hi = prod_pct * discount[0], prod_pct * discount[1]
    return {"blend": blend, "prod_pct": prod_pct, "lo_pct": lo, "hi_pct": hi,
            "lo": lo * next_cap, "hi": hi * next_cap, "cap": next_cap,
            "one_season": vorp_prior is None}


def deal_caps(start_k: int, n: int, growth: float, caps=None) -> tuple[list[float], int]:
    out = [float(c) for c in (caps or [])]
    projected = 0
    k = start_k + len(out)
    while len(out) < n:
        c, proj = cap_for(k, growth)
        out.append(c); projected += proj; k += 1
    return out[:n], projected


def summarize_salaries(salaries: list[float], caps: list[float]) -> dict:
    if not salaries or any((not math.isfinite(s)) or s < 0 for s in salaries):
        raise ValueError("Salaries must be finite and nonnegative")
    if len(caps) != len(salaries) or any((not math.isfinite(c)) or c <= 0 for c in caps):
        raise ValueError("Need one positive cap per salary season")
    total, n = sum(salaries), len(salaries)
    shares = [s / c for s, c in zip(salaries, caps)]
    return {"total": total, "aav": total / n, "years": n,
            "start_share": salaries[0] / caps[0],
            "aav_over_start_cap": total / n / caps[0],
            "mean_annual_share": sum(shares) / n,
            "aggregate_share": total / sum(caps)}


# ---------- output ----------
def money(x: float) -> str:
    return f"${x / 1e6:,.1f}M"


def tier_note(pct: float) -> str:
    cleared = [t for t in MAX_TIERS if pct >= t]
    return f"clears {int(cleared[-1] * 100)}% max" if cleared else "below 25% max"


def report(a) -> int:
    k = season_key(a.season)
    vorp = to_vorp(a.vorp, a.bpm, a.mp)
    if vorp is None:
        raise SystemExit("Need --vorp, or --bpm and --mp")
    prior = to_vorp(a.prior_vorp, a.prior_bpm, a.prior_mp)
    cap = a.cap or cap_for(k, a.growth)[0]
    kw = {"payroll_ratio": a.payroll_ratio, "min_ratio": a.min_ratio}
    sv = season_value(cap, vorp, **kw)

    name = a.name or "Player"
    print(f"{name}  |  {season_label(k)}")
    print("\n1. Production value (that season's cap)")
    print(f"- VORP {sv['vorp']:.2f} -> WAR {sv['war']:.2f} at {money(sv['dpw'])}/win, cap {money(cap)}")
    print(f"- Value {money(sv['value'])} = {sv['pct']:.1%} of cap ({tier_note(sv['pct'])})")
    if a.salary is not None:
        print(f"- Salary {money(a.salary)} -> surplus {money(sv['value'] - a.salary)}")

    nk = season_key(a.deal_start) if a.deal_start else k + 1  # market priced at deal start
    ncap, nproj = (a.next_cap, False) if a.next_cap else cap_for(nk, a.growth)
    m = market_estimate(vorp, prior, ncap, tuple(a.discount), **kw)
    print(f"\n2. Market estimate (new deal starting {season_label(nk)}, AAV basis)")
    src = "last season only, no prior given" if m["one_season"] else f"0.6 x {vorp:.2f} + 0.4 x {prior:.2f}"
    print(f"- Blended VORP {m['blend']:.2f} ({src})")
    print(f"- Production {m['prod_pct']:.1%} of cap x discount {a.discount[0]:.2f}-{a.discount[1]:.2f}")
    print(f"- Expected AAV {money(m['lo'])} to {money(m['hi'])} "
          f"({m['lo_pct']:.1%}-{m['hi_pct']:.1%} of {money(ncap)} cap{', projected' if nproj else ''})")

    if a.deal:
        start = nk
        caps, nproj = deal_caps(start, len(a.deal), a.growth, a.deal_caps)
        d = summarize_salaries(a.deal, caps)
        print(f"\n3. Deal check ({d['years']} yr, {money(d['total'])} from {season_label(start)})")
        print(f"- AAV {money(d['aav'])}  |  start share {d['start_share']:.1%}  |  AAV/start cap {d['aav_over_start_cap']:.1%}")
        print(f"- Mean annual share {d['mean_annual_share']:.1%}  |  aggregate share {d['aggregate_share']:.1%}")
        if nproj:
            print(f"- {nproj} of {len(caps)} caps projected at {a.growth:.1%} growth")
        r = d["aav_over_start_cap"]
        where = "inside" if m["lo_pct"] <= r <= m["hi_pct"] else ("above" if r > m["hi_pct"] else "below")
        print(f"- AAV/start cap {r:.1%} is {where} the market range")
    return 0


def report_csv(a) -> int:
    """CSV columns: name,season,vorp,bpm,mp,salary[,prior_vorp]. Prior season is
    auto-filled from another row for the same player if prior_vorp is blank."""
    with open(a.csv, newline="") as f:
        rows = list(csv.DictReader(f))
    f2 = lambda r, c: float(r[c]) if r.get(c) not in (None, "") else None
    seen = {}
    for r in rows:
        r["_k"] = season_key(r["season"])
        r["_v"] = to_vorp(f2(r, "vorp"), f2(r, "bpm"), f2(r, "mp"))
        seen[(r["name"].strip(), r["_k"])] = r["_v"]
    print(f"{'Player':<22}{'Season':<9}{'VORP':>6}{'Value':>10}{'%Cap':>7}{'Surplus':>10}{'Mkt AAV next':>20}")
    for r in rows:
        if r["_v"] is None:
            print(f"{r['name']:<22}{r['season']:<9}  missing vorp or bpm+mp"); continue
        k, v = r["_k"], r["_v"]
        sv = season_value(cap_for(k, a.growth)[0], v)
        prior = f2(r, "prior_vorp")
        if prior is None:
            prior = seen.get((r["name"].strip(), k - 1))
        m = market_estimate(v, prior, cap_for(k + 1, a.growth)[0], tuple(a.discount))
        sal = f2(r, "salary")
        sur = money(sv["value"] - sal) if sal is not None else "-"
        mk = f"{money(m['lo'])}-{money(m['hi'])}" + ("*" if m["one_season"] else "")
        print(f"{r['name']:<22}{season_label(k):<9}{v:>6.2f}{money(sv['value']):>10}{sv['pct']:>7.1%}{sur:>10}{mk:>20}")
    print("* no prior season found; market estimate uses one season")
    return 0


# ---------- selftest ----------
def selftest() -> int:
    ok = fail = 0

    def check(label, cond):
        nonlocal ok, fail
        ok += bool(cond); fail += not cond
        print(f"{'PASS' if cond else 'FAIL'}  {label}")

    def raises(fn):
        try:
            fn(); return False
        except ValueError:
            return True

    check("VORP formula: Duren BPM 5.0, 1976 min -> 3.51", abs(to_vorp(bpm=5.0, mp=1976) - 3.51) < 0.01)
    check("VORP formula: Thompson BPM 3.1, 1896 min -> 2.46", abs(to_vorp(bpm=3.1, mp=1896) - 2.46) < 0.01)
    cap = CAP_BY_SEASON[2026]
    sv = season_value(cap, 3.51)
    check("Duren 2025-26 value ~$58.3M, ~37.7% of cap", abs(sv["value"] / 1e6 - 58.3) < 0.3 and abs(sv["pct"] - 0.377) < 0.003)
    check("Replacement (VORP 0) = minimum salary", abs(season_value(cap, 0)["value"] - 2_296_274) < 1)
    team = LEAGUE_WAR_PER_TEAM * sv["dpw"] + 15 * MIN_RATIO * cap
    check("Identity: 27 WAR x $/win + 15 minimums = avg payroll", abs(team - 193_900_000) < 1)
    m = market_estimate(3.5, 2.7, CAP_BY_SEASON[2027])
    check("Duren blend 0.6x3.5 + 0.4x2.7 = 3.18", abs(m["blend"] - 3.18) < 1e-9)
    check("Duren market AAV inside reported $35-44M", 35e6 <= m["lo"] and m["hi"] <= 44e6)
    check("No prior -> one-season flag", market_estimate(3.5, None, cap)["one_season"])
    check("Projected cap 2028 = 2027 x 1.055", cap_for(2028) == (CAP_BY_SEASON[2027] * 1.055, True))
    check("Cap before table raises", raises(lambda: cap_for(2010)))
    sengun = [31_896_552, 34_448_276, 37_000_000, 39_551_724, 42_103_448]
    caps, nproj = deal_caps(2026, 5, DEFAULT_GROWTH)
    d = summarize_salaries(sengun, caps)
    check("Sengun: 3 of 5 caps projected", nproj == 3)
    check("Sengun AAV/start cap 23.9%", abs(d["aav_over_start_cap"] - 0.23925) < 0.0005)
    check("Sengun start share 20.6%", abs(d["start_share"] - 0.20625) < 0.0005)
    check("Sengun aggregate share 21.2%", abs(d["aggregate_share"] - 0.212) < 0.001)
    agg = summarize_salaries([10, 20], [100, 300])
    check("Aggregate share [10,20]/[100,300] = 0.075", abs(agg["aggregate_share"] - 0.075) < 1e-12)
    check("Mean annual share [10,20]/[100,300] = 0.0833", abs(agg["mean_annual_share"] - (0.1 + 20 / 300) / 2) < 1e-12)
    check("Explicit deal caps used first", deal_caps(2026, 2, 0, [1.0])[0] == [1.0, CAP_BY_SEASON[2027]])
    check("Rejects empty salaries", raises(lambda: summarize_salaries([], [])))
    check("Rejects negative salary", raises(lambda: summarize_salaries([-1], [100])))
    check("Rejects cap count mismatch", raises(lambda: summarize_salaries([1, 2], [100])))
    check("Rejects zero cap", raises(lambda: summarize_salaries([1], [0])))
    check("Season key parsing", season_key("2025-26") == 2026 == season_key(2026))
    print(f"\n{ok} PASS, {fail} FAIL")
    return 1 if fail else 0


# ---------- CLI ----------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="NBA player value: production, market estimate, deal check. One command.")
    ap.add_argument("--name")
    ap.add_argument("--season", help="season valued, e.g. 2025-26")
    ap.add_argument("--vorp", type=float); ap.add_argument("--bpm", type=float); ap.add_argument("--mp", type=float)
    ap.add_argument("--prior-vorp", type=float, help="VORP the season before (for the market blend)")
    ap.add_argument("--prior-bpm", type=float); ap.add_argument("--prior-mp", type=float)
    ap.add_argument("--salary", type=float, help="salary in the valued season (for surplus)")
    ap.add_argument("--deal", type=float, nargs="+", metavar="SAL", help="real deal salaries, year by year")
    ap.add_argument("--deal-start", help="first season of the deal; market estimate is priced here (default: season after --season)")
    ap.add_argument("--deal-caps", type=float, nargs="+", help="override caps for the deal's first seasons")
    ap.add_argument("--cap", type=float, help="override the valued season's cap")
    ap.add_argument("--next-cap", type=float, help="override next season's cap (market estimate)")
    ap.add_argument("--growth", type=float, default=DEFAULT_GROWTH, help="cap growth for seasons not on file")
    ap.add_argument("--discount", type=float, nargs=2, default=list(DISCOUNT), metavar=("LO", "HI"))
    ap.add_argument("--payroll-ratio", type=float, default=PAYROLL_RATIO)
    ap.add_argument("--min-ratio", type=float, default=MIN_RATIO)
    ap.add_argument("--csv", help="batch: name,season,vorp,bpm,mp,salary[,prior_vorp]")
    ap.add_argument("--selftest", action="store_true")
    return ap


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        if a.selftest:
            return selftest()
        if a.csv:
            return report_csv(a)
        if not a.season:
            raise SystemExit("Need --season (or --csv / --selftest)")
        return report(a)
    except ValueError as e:
        raise SystemExit(f"Error: {e}")


if __name__ == "__main__":
    sys.exit(main())
