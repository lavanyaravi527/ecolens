import pytest

from backend.measure.codegen import CodegenError, program_to_c


def prog(*stmts):
    return {"body": list(stmts)}


def test_division_by_literal_is_opaque_so_gcc_cannot_rewrite_it(programs):
    c = program_to_c(programs["demo"]["before"])
    assert "OPQ(INT64_C(4))" in c          # a[j] / 4 must stay a real division
    c_after = program_to_c(programs["demo"]["after"])
    assert ">> INT64_C(2)" in c_after      # ...and the fixed version really uses the shift


def test_every_array_is_declared_and_allocated(programs):
    c = program_to_c(programs["demo"]["before"])
    assert "static int32_t *a_a;" in c and "static int32_t *a_b;" in c
    assert "eco_alloc(&a_a" in c


@pytest.mark.parametrize("bad", [
    prog({"t": "asg", "n": "x;system(0)", "e": ["num", 1]}),
    prog({"t": "asg", "n": "x", "e": ["bin", "|", ["num", 1], ["num", 2]]}),
    prog({"t": "ret", "e": ["var", "undefined_var"]}),
    prog({"t": "asg", "n": "x", "e": ["num", 1.5]}),
    prog({"t": "asg", "n": "x", "e": ["num", 2 ** 60]}),
    prog({"t": "store", "n": "nope", "ix": ["num", 0], "e": ["num", 1]}),
    prog({"t": "evil", "n": "x"}),
    {"body": "not a list"},
    {"nobody": []},
])
def test_untrusted_ast_is_rejected(bad):
    with pytest.raises(CodegenError):
        program_to_c(bad)


def test_deep_nesting_is_rejected():
    e = ["num", 1]
    for _ in range(200):
        e = ["bin", "+", e, ["num", 1]]
    with pytest.raises(CodegenError):
        program_to_c(prog({"t": "ret", "e": e}))


def test_array_and_scalar_name_clash_is_rejected():
    with pytest.raises(CodegenError):
        program_to_c(prog({"t": "asg", "n": "a", "e": ["num", 1]},
                          {"t": "alloc", "n": "a", "e": ["num", 4]}))
