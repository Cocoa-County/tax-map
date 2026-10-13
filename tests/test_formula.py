import json
import shutil
import subprocess
from pathlib import Path

import pytest

from taxmap.domain.models import Formula
from taxmap.formula import FormulaError, compile_formula, evaluate, parse, round_half_away

ROOT = Path(__file__).resolve().parents[1]
FORMULA_JS = ROOT / "src" / "taxmap" / "site" / "static" / "formula.js"


def make(expression, *, inputs=(), constants=None, lookups=None, terms=(), applies_when=None):
    return compile_formula(Formula.model_validate({
        "id": "f", "name": "F", "district": "d", "expression": expression,
        "inputs": [{"name": n, "label": n} for n in inputs],
        "constants": {k: {"value": v} for k, v in (constants or {}).items()},
        "lookups": lookups or {}, "terms": [{"name": n, "expression": e} for n, e in terms],
        "applies_when": applies_when,
    }))


LOOKUPS = {
    "edu": {"key_type": "int", "entries": [
        {"keys": "11, 12, 62-69", "value": 1.0},
        {"keys": [13, "20-22"], "value": 2.5},
    ]},
    "kind": {"key_type": "str", "default": 0.5, "entries": [{"keys": ["A", "B"], "value": 3}]},
}

# (expression, inputs, expected value) shared by the Python and JS evaluators.
CASES = [
    ("1 + 2 * 3", {}, 7.0),
    ("(1 + 2) * 3", {}, 9.0),
    ("2 ^ 3 ^ 2", {}, 512.0),
    ("-2 ^ 2", {}, -4.0),
    ("7 % 3", {}, 1.0),
    ("round(2.5)", {}, 3.0),
    ("round(-2.5)", {}, -3.0),
    ("round(1.005, 2)", {}, 1.01),
    ("round(T * s / S, 2)", {"s": 1234}, 104.25),
    ("floor(2.7) + ceil(2.1) + abs(-1)", {}, 6.0),
    ("min(3, 1, 2) + max(3, 1, 2)", {}, 4.0),
    ("if(x > 5, 10, 20)", {"x": 6}, 10.0),
    ("if(x > 5, 10, 20)", {"x": "5"}, 20.0),
    ("coalesce(x, 7)", {"x": None}, 7.0),
    ("coalesce(x, 7)", {"x": ""}, 7.0),
    ("coalesce(x, 7)", {"x": 0}, 0.0),
    ("present(x)", {"x": None}, 0.0),
    ("present(x)", {"x": 3}, 1.0),
    ("lookup(edu, code)", {"code": 64}, 1.0),
    ("lookup(edu, code)", {"code": "21"}, 2.5),
    ("lookup(edu, code)", {"code": 21.0}, 2.5),
    ("lookup(kind, k)", {"k": "B"}, 3.0),
    ("lookup(kind, k)", {"k": "Z"}, 0.5),
    ("x == 0 or 10 / x > 1", {"x": 0}, 1.0),
    ("x != 0 and 10 / x > 1", {"x": 0}, 0.0),
    ("not (x > 1)", {"x": 0}, 1.0),
    ("if(k == \"A\", 1, 2)", {"k": "A"}, 1.0),
    ("rate * 100", {}, 25.0),
]


def run_case(expression, values):
    compiled = make(expression, inputs=["s", "x", "code", "k"], constants={"T": 61755.46, "S": 731026.0},
                    lookups=LOOKUPS, terms=[("rate", "1 / 4")])
    full = {"s": None, "x": None, "code": None, "k": None, **values}
    return compiled, full, evaluate(compiled, full)


@pytest.mark.parametrize("expression,values,expected", CASES)
def test_python_evaluator(expression, values, expected):
    _, _, result = run_case(expression, values)
    assert result["value"] == pytest.approx(expected), result


def test_round_half_away():
    assert round_half_away(0.125, 2) == 0.13
    assert round_half_away(-0.125, 2) == -0.13
    assert round_half_away(2.675, 2) == 2.68


def test_missing_input_reported():
    result = evaluate(make("x * 2", inputs=["x"]), {"x": None})
    assert result["value"] is None and "x" in result["missing"]


def test_lookup_miss_without_default_is_missing():
    _, _, result = run_case("lookup(edu, code)", {"code": 99})
    assert result["value"] is None and ("missing" in result or "error" in result)


def test_applies_when_false():
    result = evaluate(make("10", inputs=["x"], applies_when="x > 1"), {"x": 0})
    assert result == {**result, "applicable": False, "value": 0.0}


def test_division_by_zero_is_error():
    result = evaluate(make("1 / x", inputs=["x"]), {"x": 0})
    assert result["value"] is None and "error" in result


@pytest.mark.parametrize("bad", ["1 +", "foo(1)", "(1", "1 2", "__import__('os')"])
def test_parse_errors(bad):
    with pytest.raises(FormulaError):
        parse(bad)


def test_unknown_name_rejected():
    with pytest.raises(FormulaError):
        make("y + 1", inputs=["x"])


def test_reserved_and_duplicate_names_rejected():
    with pytest.raises(FormulaError):
        make("1", inputs=["min"])
    with pytest.raises(FormulaError):
        make("1", inputs=["a"], constants={"a": 1})


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_parity(tmp_path):
    payload = []
    expected = []
    for expression, values in [(c[0], c[1]) for c in CASES] + [("lookup(edu, code)", {"code": 99}),
                                                                 ("1 / x", {"x": 0}), ("x * 2", {})]:
        compiled, full, result = run_case(expression, values)
        payload.append({"data": compiled.data, "values": full})
        expected.append(result)
    (tmp_path / "cases.json").write_text(json.dumps(payload), encoding="utf-8")
    script = (
        "const f = require(process.argv[1]); const fs = require('fs');"
        "const cases = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));"
        "console.log(JSON.stringify(cases.map(c => f.evaluate(c.data, c.values))));"
    )
    out = subprocess.run(["node", "-e", script, str(FORMULA_JS), str(tmp_path / "cases.json")],
                         capture_output=True, text=True, check=True).stdout
    for py, js in zip(expected, json.loads(out)):
        assert js["applicable"] == py["applicable"]
        if py["value"] is None:
            assert js["value"] is None
            assert ("missing" in js) == ("missing" in py) and ("error" in js) == ("error" in py)
        else:
            assert js["value"] == pytest.approx(py["value"], abs=1e-9)
        assert js["terms"] == pytest.approx(py["terms"])
