"""Translate an EcoLens AST (as produced by the frontend parser) into C.

The AST is untrusted input, so everything is validated against a strict
whitelist before a single character of C is emitted: identifiers must match
[A-Za-z_][A-Za-z0-9_]*, numbers must be integers, operators come from a fixed
set, and size limits apply. No user-supplied string is ever pasted into C
without passing those checks.

Semantics (mirroring the frontend): scalars are 64-bit integers, arrays hold
32-bit integers (the cost model assumes 4 bytes per element) and are
zero-initialised by `array(n)`. `/` and `%` follow C (truncating) semantics.

The generated program is meant to be compiled with -O0 so that the machine
code mirrors the source. Literal operands of * / % are routed through an
opaque macro, otherwise gcc would turn `x / 4` into a shift by itself and the
"divide -> shift" fix could not be measured.
"""
from __future__ import annotations

import re

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
BINOPS = {"+", "-", "*", "/", "%", "<<", ">>", "&"}
OPAQUE_OPS = {"*", "/", "%"}
MAX_NODES = 20_000
MAX_DEPTH = 64
MAX_INT = 2**53


class CodegenError(ValueError):
    pass


class _Gen:
    def __init__(self) -> None:
        self.nodes = 0
        self.arrays: set[str] = set()
        self.scalars: set[str] = set()
        self.defined: set[str] = set()
        self.tmp = 0

    # ---- validation helpers ----
    def tick(self, depth: int) -> None:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise CodegenError("program too large")
        if depth > MAX_DEPTH:
            raise CodegenError("expression/loop nesting too deep")

    @staticmethod
    def ident(x, what: str) -> str:
        if not isinstance(x, str) or not IDENT.match(x) or len(x) > 64:
            raise CodegenError(f"invalid {what} name: {x!r}")
        return x

    @staticmethod
    def integer(x) -> int:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise CodegenError(f"invalid number: {x!r}")
        if isinstance(x, float):
            if not x.is_integer():
                raise CodegenError(f"only integers are supported, got {x}")
            x = int(x)
        if abs(x) > MAX_INT:
            raise CodegenError("number out of range")
        return x

    # ---- expressions ----
    def lit(self, v: int, opaque: bool) -> str:
        c = f"INT64_C({v})" if v >= 0 else f"(-INT64_C({-v}))"
        return f"OPQ({c})" if opaque else c

    def expr(self, e, depth: int = 0, opaque: bool = False) -> str:
        self.tick(depth)
        if not isinstance(e, list) or not e:
            raise CodegenError("malformed expression")
        k = e[0]
        if k == "num" and len(e) == 2:
            return self.lit(self.integer(e[1]), opaque)
        if k == "var" and len(e) == 2:
            n = self.ident(e[1], "variable")
            if n in self.arrays:
                raise CodegenError(f"{n} is an array, not a number")
            if n not in self.defined:
                raise CodegenError(f"variable {n} is used before it is defined")
            return f"s_{n}"
        if k == "idx" and len(e) == 3:
            a = self.ident(e[1], "array")
            if a not in self.arrays:
                raise CodegenError(f"{a} is not an array")
            return f"((int64_t)a_{a}[{self.expr(e[2], depth + 1)}])"
        if k == "bin" and len(e) == 4:
            op = e[1]
            if op not in BINOPS:
                raise CodegenError(f"unsupported operator {op!r}")
            o = op in OPAQUE_OPS
            return f"({self.expr(e[2], depth + 1, o)} {op} {self.expr(e[3], depth + 1, o)})"
        raise CodegenError("malformed expression")

    # ---- statements ----
    def block(self, body, depth: int, ind: str) -> list[str]:
        if not isinstance(body, list):
            raise CodegenError("malformed block")
        out: list[str] = []
        for s in body:
            self.tick(depth)
            if not isinstance(s, dict):
                raise CodegenError("malformed statement")
            t = s.get("t")
            if t == "alloc":
                n = self.ident(s.get("n"), "array")
                size = self.expr(s["e"])
                if n in self.scalars:
                    raise CodegenError(f"{n} used as both array and number")
                self.arrays.add(n)
                out.append(f"{ind}eco_alloc(&a_{n}, &cap_{n}, {size});")
            elif t == "asg":
                n = self.ident(s.get("n"), "variable")
                if n in self.arrays:
                    raise CodegenError(f"{n} used as both array and number")
                rhs = self.expr(s["e"])
                self.scalars.add(n)
                self.defined.add(n)
                out.append(f"{ind}s_{n} = {rhs};")
            elif t == "store":
                n = self.ident(s.get("n"), "array")
                if n not in self.arrays:
                    raise CodegenError(f"{n} is not an array")
                out.append(f"{ind}a_{n}[{self.expr(s['ix'])}] = (int32_t)({self.expr(s['e'])});")
            elif t == "ret":
                out.append(f"{ind}return {self.expr(s['e'])};")
            elif t == "for":
                v = self.ident(s.get("v"), "loop variable")
                if v in self.arrays:
                    raise CodegenError(f"{v} used as both array and number")
                lo, hi = self.expr(s["lo"]), self.expr(s["hi"])
                self.scalars.add(v)
                self.defined.add(v)
                self.tmp += 1
                h = f"hi_{self.tmp}"
                out.append(f"{ind}{{ int64_t {h} = {hi};")
                out.append(f"{ind}for (s_{v} = {lo}; s_{v} < {h}; s_{v}++) {{")
                out += self.block(s["body"], depth + 1, ind + "  ")
                out.append(f"{ind}}} }}")
            else:
                raise CodegenError(f"unknown statement type {t!r}")
        return out


PRELUDE = """\
#include "ecolens_rt.h"
#define OPQ(n) ({ int64_t t_ = (n); __asm__ volatile("" : "+r"(t_)); t_; })

static void eco_alloc_impl(int32_t **p, int64_t *cap, int64_t n) {
    if (n < 0 || n > (1LL << 27)) { fprintf(stderr, "array size %lld out of range\\n", (long long)n); exit(7); }
    if (n > *cap) { free(*p); *p = (int32_t *)malloc((size_t)(n ? n : 1) * 4); *cap = n;
                    if (!*p) { fprintf(stderr, "out of memory\\n"); exit(5); } }
    memset(*p, 0, (size_t)n * 4);
}
#define eco_alloc(p, c, n) eco_alloc_impl((p), (c), (n))
"""


def program_to_c(prog: dict) -> str:
    if not isinstance(prog, dict) or not isinstance(prog.get("body"), list):
        raise CodegenError("expected {body: [...]}")
    g = _Gen()
    body = g.block(prog["body"], 0, "  ")
    decls = [f"static int32_t *a_{a}; static int64_t cap_{a};" for a in sorted(g.arrays)]
    locals_ = [f"  int64_t s_{s} = 0;" for s in sorted(g.scalars)]
    return "\n".join([
        PRELUDE, *decls, "",
        "static int64_t eco_run(void) {", *locals_, *body, "  return 0;", "}", "",
        "int main(int argc, char **argv) { return eco_harness(eco_run, argc, argv); }", "",
    ])
