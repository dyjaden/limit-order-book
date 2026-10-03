"""Day 5, Step 3: the registry is append-only, and it says so loudly.

Against a temporary path: ids increment, a second append leaves the
first row byte-identical, the header is written exactly once, an empty
question or metric is refused, the caller cannot assign ids or
timestamps, a hand-edited file is refused on read, and ``count`` is the
number of rows.
"""
import pytest

from lob.registry import COLUMNS, count, init, log_trial, read


def trial(**over):
    base = dict(script="test", question="does x move y", ticker="AAPL",
                date="2012-06-21", clock="calendar", bucket=10, horizon=0,
                window="09:30-16:00", split="none", n_obs=2_340,
                metric="r2", value=0.25, status="descriptive", note="")
    base.update(over)
    return base


def test_ids_increment_and_the_first_row_is_never_touched(tmp_path):
    reg = tmp_path / "trials.csv"
    assert count(reg) == 0 and read(reg) == []
    assert log_trial(reg, **trial()) == 1
    first = reg.read_bytes()
    assert first.count(b"\n") == 2                       # header + one row
    assert log_trial(reg, **trial(metric="hit_rate", value=0.8)) == 2
    assert reg.read_bytes().startswith(first)            # byte-identical prefix
    assert reg.read_text().count("trial_id") == 1        # header exactly once
    rows = read(reg)
    assert [r["trial_id"] for r in rows] == ["1", "2"]
    assert rows[0]["metric"] == "r2" and rows[0]["value"] == "0.25"
    assert rows[1]["value"] == "0.8"
    assert count(reg) == 2
    assert list(rows[0]) == list(COLUMNS)


def test_empty_question_or_metric_is_refused(tmp_path):
    reg = tmp_path / "trials.csv"
    with pytest.raises(ValueError):
        log_trial(reg, **trial(question="  "))
    with pytest.raises(ValueError):
        log_trial(reg, **trial(metric=""))
    with pytest.raises(ValueError):
        log_trial(reg, **{k: v for k, v in trial().items() if k != "question"})
    assert not reg.exists()                              # nothing was written


def test_status_columns_and_ids_are_policed(tmp_path):
    reg = tmp_path / "trials.csv"
    with pytest.raises(ValueError):
        log_trial(reg, **trial(status="exploratory"))
    with pytest.raises(ValueError):
        log_trial(reg, **trial(colour="blue"))
    with pytest.raises(ValueError):
        log_trial(reg, trial_id=7, **trial())
    with pytest.raises(ValueError):
        log_trial(reg, logged_at="yesterday", **trial())
    assert log_trial(reg, **trial(status="abandoned", note="sign flipped; see #1")) == 1
    assert read(reg)[0]["status"] == "abandoned"
    # status defaults to descriptive, None becomes an empty cell
    log_trial(reg, **{k: v for k, v in trial(value=None).items() if k != "status"})
    assert read(reg)[1]["status"] == "descriptive" and read(reg)[1]["value"] == ""


def test_a_hand_edited_registry_is_refused_on_read(tmp_path):
    reg = tmp_path / "trials.csv"
    for _ in range(3):
        log_trial(reg, **trial())
    lines = reg.read_text().splitlines(keepends=True)
    reg.write_text("".join(lines[:2] + lines[3:]))       # someone deleted row 2
    with pytest.raises(ValueError):
        count(reg)


def test_logged_at_is_utc_to_the_second(tmp_path):
    reg = tmp_path / "trials.csv"
    log_trial(reg, **trial())
    stamp = read(reg)[0]["logged_at"]
    assert len(stamp) == 20 and stamp.endswith("Z") and stamp[10] == "T"


def test_init_writes_the_header_once_and_leaves_an_existing_registry_alone(tmp_path):
    reg = tmp_path / "trials.csv"
    assert init(reg) == reg
    assert reg.read_text() == ",".join(COLUMNS) + "\n"
    assert count(reg) == 0
    log_trial(reg, **trial())
    before = reg.read_bytes()
    init(reg)
    assert reg.read_bytes() == before
    assert reg.read_text().count("trial_id") == 1
