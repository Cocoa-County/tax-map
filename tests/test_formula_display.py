import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from taxmap.domain.models import Formula
from taxmap.formula import compile_formula, evaluate

ROOT = Path(__file__).resolve().parents[1]
DISPLAY_JS = ROOT / "src" / "taxmap" / "site" / "static" / "formula-display.js"
NS = {"m": "http://www.w3.org/1998/Math/MathML"}


def render(formula, values, tmp_path):
    compiled = compile_formula(Formula.model_validate(formula))
    result = evaluate(compiled, values)
    payload = tmp_path / "display.json"
    payload.write_text(json.dumps({"formula": compiled.data, "values": values, "result": result}), encoding="utf-8")
    script = """
const d = require(process.argv[1]);
const p = JSON.parse(require('fs').readFileSync(process.argv[2], 'utf8'));
const symbols = d.symbols(p.formula, p.values, p.result);
console.log(JSON.stringify({
  symbolic: d.equation(p.formula.ast, symbols),
  live: d.equation(p.formula.ast, symbols, {substitute: true, value: p.result.value}),
  symbols
}));
"""
    return json.loads(subprocess.run(["node", "-e", script, str(DISPLAY_JS), str(payload)],
                                    capture_output=True, text=True, encoding="utf-8", check=True).stdout)


pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def test_fraction_live_values_and_symbol_metadata(tmp_path):
    data = render({
        "id": "f", "name": "F", "district": "d",
        "inputs": [{"name": "s_i", "label": "Home size", "unit": "sq ft"}],
        "constants": {"T": {"value": 60, "label": "District total"}, "S": {"value": 6000}},
        "terms": [{"name": "r", "label": "Rate", "expression": "T / S"}],
        "expression": "round(T * s_i / S, 2)",
    }, {"s_i": 2000}, tmp_path)
    symbolic = ElementTree.fromstring(data["symbolic"])
    live = ElementTree.fromstring(data["live"])
    assert symbolic.find(".//m:mfrac", NS) is not None
    assert symbolic.find(".//m:msub", NS) is not None
    assert "2,000" in "".join(live.itertext())
    assert "".join(live.itertext()).endswith("=20")
    assert data["symbols"]["s_i"]["kind"] == "input"
    assert data["symbols"]["T"]["kind"] == "constant"
    assert data["symbols"]["r"]["value"] == pytest.approx(0.01)
    assert 'tabindex="0"' in data["symbolic"] and 'aria-label="Home size' in data["symbolic"]


@pytest.mark.parametrize("expression,tag", [
    ("if(x > 1, x ^ 2, abs(x))", "mtable"),
    ("floor(x) + ceil(x)", "mo"),
    ("not (x == 1) and x != 0", "mo"),
    ("min(x, 2) + max(x, 3) + coalesce(x, 0)", "mi"),
])
def test_expression_shapes_are_valid_mathml(expression, tag, tmp_path):
    data = render({"id": "f", "name": "F", "district": "d", "expression": expression,
                   "inputs": [{"name": "x", "label": "X"}]}, {"x": 2}, tmp_path)
    assert ElementTree.fromstring(data["symbolic"]).find(f".//m:{tag}", NS) is not None
    ElementTree.fromstring(data["live"])


def test_missing_values_and_untrusted_text(tmp_path):
    data = render({
        "id": "f", "name": "F", "district": "d", "expression": "x + 1",
        "inputs": [{"name": "x", "label": '<img src=x onerror="alert(1)">'}],
    }, {"x": None}, tmp_path)
    live = ElementTree.fromstring(data["live"])
    assert "?" in "".join(live.itertext())
    assert "<img" not in data["live"]
    assert data["symbols"]["x"]["value"] is None


def test_lookup_name_is_not_substituted_and_strings_are_escaped(tmp_path):
    data = render({
        "id": "f", "name": "F", "district": "d", "expression": "lookup(kinds, code)",
        "inputs": [{"name": "code", "label": "Code", "type": "text"}],
        "lookups": {"kinds": {"key_type": "str", "entries": [{"keys": ["A&B"], "value": 2}]}},
    }, {"code": "A&B"}, tmp_path)
    assert "kinds" in "".join(ElementTree.fromstring(data["live"]).itertext())
    assert "A&amp;B" in data["live"]


@pytest.mark.parametrize("expression,expected", [
    ("x + x * 2", "Assessment=x+x×2"),
    ("(x + 1) * 2", "Assessment=(x+1)×2"),
    ("x - (x - 1)", "Assessment=x−(x−1)"),
    ("(x ^ 2) ^ 3", "Assessment=(x2)3"),
    ("-x ^ 2", "Assessment=−x2"),
])
def test_grouping_matches_ast_without_excess_parentheses(expression, expected, tmp_path):
    data = render({"id": "f", "name": "F", "district": "d", "expression": expression,
                   "inputs": [{"name": "x", "label": "X"}]}, {"x": 2}, tmp_path)
    assert "".join(ElementTree.fromstring(data["symbolic"]).itertext()) == expected
