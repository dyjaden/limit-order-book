"""The trial registry: every question ever asked of this data, counted.

Standing rule 3 of the plan says the registry exists from the first
analysis run, not retrofitted, and this module is that rule as code.
Every regression, correlation, hit rate or slope computed on the data
from now on gets one row in ``results/trials.csv``: what was asked, on
what clock, at what bucket size, over what window, with what split, how
many observations, and what came out. Rows are appended whether the
result is good, bad or abandoned, and nothing is ever rewritten. Week
10's deflated evaluation divides by the length of this file, which is
why the rows that look embarrassing are the ones that matter most.

The rule the module enforces, and the test pins: **a number is logged
before it is printed.** A caller passes the result to ``log_trial`` the
moment it has it, before any formatting, so there is no moment at which
an unlogged result exists on screen to be liked or disliked. A trial
that is abandoned is logged as abandoned with the reason in the note.
The file is whitelisted in ``.gitignore`` and committed every time it
grows; a registry that is not in the repository is not a registry.

Columns, one row per trial:

    trial_id   1, 2, 3, ... assigned here, never by the caller
    logged_at  UTC, to the second
    script     the script that asked
    question   what was asked, in words
    ticker, date
    clock      'calendar' | 'event' | 'none'
    bucket     bin width in seconds, or updates per bucket
    horizon    0 for contemporaneous; seconds ahead for Week 6
    window     the data window: '09:30-16:00', a half hour, ...
    split      'none' | 'first-half/second-half' | 'day A/day B'
    n_obs      observations the number was computed from
    metric     what ``value`` is: 'r2', 'hit_rate', 'slope', ...
    value      the number, as the caller computed it
    status     'descriptive' | 'predictive' | 'abandoned'
    note       everything else worth keeping, in one cell
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

REGISTRY = Path("results/trials.csv")
COLUMNS = ("trial_id", "logged_at", "script", "question", "ticker", "date",
           "clock", "bucket", "horizon", "window", "split", "n_obs",
           "metric", "value", "status", "note")
STATUSES = ("descriptive", "predictive", "abandoned")
REQUIRED = ("question", "metric")


def read(path: Path = REGISTRY) -> list[dict]:
    """Every row, as written. A missing file is an empty registry."""
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows, start=1):
        if int(r["trial_id"]) != i:
            raise ValueError(f"{path}: row {i} carries trial_id {r['trial_id']}; "
                             f"the registry has been edited")
    return rows


def count(path: Path = REGISTRY) -> int:
    """The N Week 10 divides by."""
    return len(read(path))


def log_trial(path: Path = REGISTRY, **fields) -> int:
    """Append ONE row with the next id and a UTC timestamp, and return
    the id so the caller can print "trial #N" beside the number. Refuses
    a row whose question or metric is empty, an unknown status, an
    unknown column, or a caller-supplied id or timestamp; never rewrites
    anything already in the file."""
    for key in ("trial_id", "logged_at"):
        if key in fields:
            raise ValueError(f"{key} is assigned by the registry, not the caller")
    unknown = set(fields) - set(COLUMNS)
    if unknown:
        raise ValueError(f"unknown registry columns: {sorted(unknown)}")
    for key in REQUIRED:
        if not str(fields.get(key, "")).strip():
            raise ValueError(f"a trial needs a non-empty {key}")
    status = fields.get("status", "descriptive")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, not {status!r}")
    fields["status"] = status
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    trial_id = count(path) + 1
    row = {c: "" for c in COLUMNS}
    row.update({k: _cell(v) for k, v in fields.items()})
    row["trial_id"] = trial_id
    row["logged_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow(row)
    return trial_id


def _cell(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        return repr(v)                    # round-trips exactly
    return str(v)


def init(path: Path = REGISTRY) -> Path:
    """Create an empty registry (header only) if there is none, so the
    file exists and is committed before the first trial is ever run.
    Idempotent: an existing registry is left exactly as it is."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n").writeheader()
    return path


def main() -> None:
    """``python -m lob.registry``: make sure the registry exists and say
    how long it is."""
    path = init()
    print(f"{path}: {count(path)} trials")


if __name__ == "__main__":
    main()
