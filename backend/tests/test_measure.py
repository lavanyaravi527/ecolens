import pytest

from backend.measure import service
from .helpers import needs_cc


@needs_cc
@pytest.mark.parametrize("name", ["demo", "stride"])
def test_before_and_after_return_the_same_value_and_energy_is_positive(programs, data_dir, name):
    r = service.measure(programs[name]["before"], programs[name]["after"], reps=2, target=0.05,
                        profile={"power_assumed_w": 10})
    assert r["same_result"] is True
    assert r["before"]["energy_pj"] > 0 and r["after"]["energy_pj"] > 0
    assert r["before"]["result_stable"]


@needs_cc
def test_division_by_zero_is_reported_not_crashed(data_dir):
    p = {"body": [{"t": "asg", "n": "z", "e": ["num", 0]},
                  {"t": "ret", "e": ["bin", "/", ["num", 5], ["var", "z"]]}]}
    with pytest.raises(service.MeasureError, match="crash"):
        service.measure(p, None, reps=1, target=0.05, profile={"power_assumed_w": 10})


def test_bad_program_is_a_measure_error_not_an_exception(data_dir):
    with pytest.raises(service.MeasureError, match="Cannot translate"):
        service.measure({"body": [{"t": "asg", "n": "a b", "e": ["num", 1]}]}, None)
