#!/usr/bin/env python3
"""NBA salary arithmetic only; no legal eligibility or valuation fitting."""
import argparse
from decimal import Decimal, InvalidOperation, localcontext
import json


def number(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Use finite numeric inputs") from exc
    if not result.is_finite():
        raise ValueError("Use finite numeric inputs")
    return result


def positive(value, label):
    result = number(value)
    if result <= 0:
        raise ValueError(f"{label} must be positive")
    return result


def schedule(cap, years, raise_pct, share=None, total=None):
    cap = positive(cap, "cap")
    if isinstance(years, bool) or not isinstance(years, int) or years < 1:
        raise ValueError("years must be a positive integer")
    if (share is None) == (total is None):
        raise ValueError("Supply exactly one of share or total")
    increment = number(raise_pct) / 100
    factors = [Decimal(1) + increment * t for t in range(years)]
    if min(factors) < 0:
        raise ValueError("Schedule would contain negative salaries")
    first = (cap * positive(share, "share") / 100 if share is not None
             else positive(total, "total") / sum(factors))
    return [first * factor for factor in factors]


def summarize(cap, salaries, caps=None):
    cap = positive(cap, "cap")
    salaries = [number(s) for s in salaries]
    if not salaries or min(salaries) < 0:
        raise ValueError("Supply one or more nonnegative salaries")
    total = sum(salaries)
    result = {
        "basis": "supplied salary amounts; arithmetic only",
        "salaries_dollars": salaries,
        "total_dollars": total,
        "aav_dollars": total / len(salaries),
        "starting_cap_share_pct": 100 * salaries[0] / cap,
        "aav_over_start_cap_pct": 100 * total / len(salaries) / cap,
    }
    if caps is not None:
        caps = [positive(c, "annual cap") for c in caps]
        if len(caps) != len(salaries):
            raise ValueError("Provide one cap for each salary season")
        if caps[0] != cap:
            raise ValueError("First annual cap must equal --cap")
        shares = [100 * s / c for s, c in zip(salaries, caps)]
        result.update({
            "caps_dollars": caps,
            "annual_cap_shares_pct": shares,
            "mean_annual_cap_share_pct": sum(shares) / len(shares),
            "aggregate_cap_share_pct": 100 * total / sum(caps),
        })
    return result


def display(value):
    if isinstance(value, Decimal):
        return format(value.quantize(Decimal("0.000001")), "f")
    if isinstance(value, list):
        return [display(v) for v in value]
    if isinstance(value, dict):
        return {k: display(v) for k, v in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("schedule", help="Fixed increments from Year 1")
    build.add_argument("--years", type=int, required=True)
    start = build.add_mutually_exclusive_group(required=True)
    start.add_argument("--share", help="Starting cap share, e.g. 21")
    start.add_argument("--total", help="Solve starting salary from total dollars")
    build.add_argument("--raise-pct", default="0", help="Increment as %% of Year 1, e.g. 8")
    actual = commands.add_parser("actual", help="Explicit supplied salary schedule")
    actual.add_argument("--salaries", nargs="+", required=True)
    for command in (build, actual):
        command.add_argument("--cap", required=True, help="Starting season cap in dollars")
        command.add_argument("--caps", nargs="+", help="Optional one cap per season")
    args = parser.parse_args()
    try:
        with localcontext() as context:
            context.prec = 40
            salaries = (schedule(args.cap, args.years, args.raise_pct, args.share, args.total)
                        if args.command == "schedule" else args.salaries)
            result = summarize(args.cap, salaries, args.caps)
            if args.command == "schedule":
                result["fixed_increment_pct_of_year_one"] = number(args.raise_pct)
            print(json.dumps(display(result), indent=2))
    except (ValueError, InvalidOperation) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
