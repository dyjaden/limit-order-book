"""Day 1, Step 1: the scaffold's own test.

Exists so CI is green from the very first push -- pytest exits nonzero on
an empty suite, and a red first build would teach the wrong lesson about
what green means. Replaced in spirit by the real invariant suite in Step 3;
kept because a package that cannot be imported is the failure mode every
later test silently assumes away.
"""
import lob


def test_package_imports_and_knows_its_version():
    assert lob.__version__ == "0.1.0"
    assert "state machine" in lob.__doc__
