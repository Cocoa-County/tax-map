"""Safe assessment-formula expression language.

Expressions are parsed into a small JSON-serializable AST (no ``eval``). The same
AST is evaluated here in Python (validation against rolls) and in the browser by
``site/static/formula.js`` (map calculator modals). Both implementations follow the
same rules so results match:

* numbers, "strings", true/false, identifiers (inputs, constants, terms)
* operators: ``or and not == != < <= > >= + - * / % ^`` (``^`` is power, right-assoc)
* functions: ``if(c, a, b)``, ``min``, ``max``, ``round(x, n)`` (half away from zero),
  ``floor``, ``ceil``, ``abs``, ``coalesce(a, b, ...)``, ``lookup(table, key)``,
  ``present(x)`` (1 if x has a value)
* a missing input raises ``MissingValue`` unless guarded by ``coalesce``/``present``/``if``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from taxmap.domain.models import Formula, Lookup
from taxmap.util import to_number

AST = list  # ["num", 1.0] | ["str", "x"] | ["var", "name"] | ["bin", op, a, b] | ["un", op, a] | ["call", f, [args]]

FUNCTIONS = {
    "if": (3, 3), "min": (1, 99), "max": (1, 99), "round": (1, 2), "floor": (1, 1), "ceil": (1, 1),
    "abs": (1, 1), "coalesce": (1, 99), "lookup": (2, 2), "present": (1, 1),
}
BINARY = {
    "or": (1, False), "and": (2, False),
    "==": (4, False), "!=": (4, False), "<": (4, False), "<=": (4, False), ">": (4, False), ">=": (4, False),
    "+": (5, False), "-": (5, False), "*": (6, False), "/": (6, False), "%": (6, False), "^": (8, True),
}
TOKEN = re.compile(r"""
    \s*(?:
      (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?)
     |(?P<str>"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')
     |(?P<name>[A-Za-z_][A-Za-z0-9_]*)
     |(?P<op>==|!=|<=|>=|[-+*/%^<>(),])
    )""", re.X)


class FormulaError(ValueError):
    pass


class MissingValue(FormulaError):
    pass


def tokenize(text: str) -> list[tuple[str, str]]:
    tokens, position = [], 0
    text = text.strip()
    while position < len(text):
        match = TOKEN.match(text, position)
        if not match or match.end() == position:
            raise FormulaError(f"Unexpected character at {position}: {text[position:position + 12]!r}")
        position = match.end()
        kind = match.lastgroup
        value = match.group(kind)
        if kind == "name" and value in {"and", "or", "not"}:
            kind = "op"
        tokens.append((kind, value))
    tokens.append(("end", ""))
    return tokens


class _Parser:
    def __init__(self, text: str):
        self.tokens = tokenize(text)
        self.i = 0

    def peek(self):
        return self.tokens[self.i]

    def take(self, value: str | None = None):
        kind, text = self.tokens[self.i]
        if value is not None and text != value:
            raise FormulaError(f"Expected {value!r} but found {text or 'end of expression'!r}")
        self.i += 1
        return kind, text

    def parse(self) -> AST:
        node = self.expression(0)
        if self.peek()[0] != "end":
            raise FormulaError(f"Unexpected {self.peek()[1]!r}")
        return node

    def expression(self, min_precedence: int) -> AST:
        left = self.unary()
        while True:
            kind, op = self.peek()
            if kind != "op" or op not in BINARY:
                return left
            precedence, right_assoc = BINARY[op]
            if precedence < min_precedence:
                return left
            self.take()
            right = self.expression(precedence if right_assoc else precedence + 1)
            left = ["bin", op, left, right]

    def unary(self) -> AST:
        kind, text = self.peek()
        if kind == "op" and text == "not":
            self.take()
            return ["un", "not", self.expression(3)]
        if kind == "op" and text in {"-", "+"}:
            self.take()
            return ["un", text, self.expression(7)]
        return self.primary()

    def primary(self) -> AST:
        kind, text = self.take()
        if kind == "num":
            return ["num", float(text)]
        if kind == "str":
            return ["str", re.sub(r"\\(.)", r"\1", text[1:-1])]
        if kind == "name":
            if text in {"true", "false"}:
                return ["num", 1.0 if text == "true" else 0.0]
            if self.peek()[1] == "(":
                self.take("(")
                args = []
                if self.peek()[1] != ")":
                    args.append(self.expression(0))
                    while self.peek()[1] == ",":
                        self.take(",")
                        args.append(self.expression(0))
                self.take(")")
                if text not in FUNCTIONS:
                    raise FormulaError(f"Unknown function {text}()")
                low, high = FUNCTIONS[text]
                if not low <= len(args) <= high:
                    raise FormulaError(f"{text}() takes {low}..{high} arguments, got {len(args)}")
                return ["call", text, args]
            return ["var", text]
        if text == "(":
            node = self.expression(0)
            self.take(")")
            return node
        raise FormulaError(f"Unexpected {text or 'end of expression'!r}")


def parse(text: str) -> AST:
    return _Parser(text).parse()


def variables(node: AST) -> set[str]:
    found: set[str] = set()

    def visit(n):
        if n[0] == "var":
            found.add(n[1])
        elif n[0] == "bin":
            visit(n[2]), visit(n[3])
        elif n[0] == "un":
            visit(n[2])
        elif n[0] == "call":
            args = n[2]
            if n[1] == "lookup":
                args = args[1:]
            for a in args:
                visit(a)

    visit(node)
    return found


# --------------------------------------------------------------------------- lookups

def expand_keys(keys: str | list, key_type: str) -> list[str]:
    raw = keys if isinstance(keys, list) else [k for k in str(keys).split(",")]
    out = []
    for part in raw:
        text = str(part).strip()
        if not text:
            continue
        match = re.fullmatch(r"(-?\d+)\s*-\s*(-?\d+)", text) if key_type == "int" else None
        if match:
            out += [str(n) for n in range(int(match.group(1)), int(match.group(2)) + 1)]
        else:
            out.append(lookup_key(text, key_type))
    return out


def text_value(value: Any) -> str:
    """String form used for lookups/equality; integral floats drop ``.0`` (matches JS)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return str(value).strip() if isinstance(value, str) else str(value)


def lookup_key(value: Any, key_type: str) -> str:
    if key_type == "int":
        number = to_number(value)
        if number is None or number != int(number):
            return text_value(value).strip()
        return str(int(number))
    return text_value(value).strip()


def compile_lookup(lookup: Lookup) -> dict:
    index: dict[str, int] = {}
    for position, entry in enumerate(lookup.entries):
        for key in expand_keys(entry.keys, lookup.key_type):
            if key in index:
                raise FormulaError(f"Lookup key {key!r} appears twice")
            index[key] = position
    return {
        "label": lookup.label, "key_type": lookup.key_type, "default": lookup.default, "index": index,
        "entries": [{"keys": e.keys if isinstance(e.keys, str) else ", ".join(map(str, e.keys)),
                     "value": e.value, "label": e.label, "details": e.details} for e in lookup.entries],
    }


# --------------------------------------------------------------------------- compile

@dataclass
class Compiled:
    formula: Formula
    data: dict  # JSON-serializable, shipped to the site

    @property
    def id(self) -> str:
        return self.formula.id


def compile_formula(formula: Formula) -> Compiled:
    names: dict[str, str] = {}
    for kind, items in (("input", [i.name for i in formula.inputs]), ("constant", list(formula.constants)),
                        ("term", [t.name for t in formula.terms]), ("lookup", list(formula.lookups))):
        for name in items:
            if name in names:
                raise FormulaError(f"{formula.id}: name {name!r} used for both {names[name]} and {kind}")
            if name in FUNCTIONS or name in {"true", "false", "and", "or", "not"}:
                raise FormulaError(f"{formula.id}: {name!r} is reserved")
            names[name] = kind
    known = {n for n, k in names.items() if k in {"input", "constant"}}

    def check(text: str, where: str) -> AST:
        try:
            node = parse(text)
        except FormulaError as error:
            raise FormulaError(f"{formula.id} {where}: {error}") from error
        _check_lookups(node, formula, where)
        unknown = variables(node) - known
        if unknown:
            raise FormulaError(f"{formula.id} {where}: unknown name(s) {sorted(unknown)}")
        return node

    terms = []
    for term in formula.terms:
        terms.append({"name": term.name, "label": term.label, "description": term.description,
                      "expression": term.expression, "ast": check(term.expression, f"term {term.name}")})
        known.add(term.name)
    data = {
        "id": formula.id, "name": formula.name, "district": formula.district, "fiscal_year": formula.fiscal_year,
        "description": formula.description, "expression": formula.expression,
        "ast": check(formula.expression, "expression"),
        "applies_when": formula.applies_when,
        "applies_ast": check(formula.applies_when, "applies_when") if formula.applies_when else None,
        "inputs": [i.dump() | {"name": i.name, "label": i.label} for i in formula.inputs],
        "constants": {k: {"value": c.value, "label": c.label, "unit": c.unit} for k, c in formula.constants.items()},
        "lookups": {k: compile_lookup(v) for k, v in formula.lookups.items()},
        "terms": terms, "tolerance": formula.tolerance, "caveats": formula.caveats,
    }
    return Compiled(formula, data)


def _check_lookups(node: AST, formula: Formula, where: str) -> None:
    if node[0] == "call":
        if node[1] == "lookup":
            table = node[2][0]
            if table[0] != "var" or table[1] not in formula.lookups:
                raise FormulaError(f"{formula.id} {where}: lookup() needs a lookup table name as first argument")
        for arg in node[2]:
            _check_lookups(arg, formula, where)
    elif node[0] == "bin":
        _check_lookups(node[2], formula, where), _check_lookups(node[3], formula, where)
    elif node[0] == "un":
        _check_lookups(node[2], formula, where)


# --------------------------------------------------------------------------- evaluate

def round_half_away(value: float, digits: int = 0) -> float:
    factor = 10.0 ** digits
    return math.copysign(math.floor(abs(value) * factor + 0.5 + 1e-9) / factor, value)


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    number = to_number(value)
    if number is None:
        raise FormulaError(f"{what} is not a number: {value!r}")
    return number


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value != ""
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(value)


def _equal(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        na, nb = to_number(a), to_number(b)
        if na is not None and nb is not None:
            return na == nb
    return text_value(a) == text_value(b)


class Evaluator:
    def __init__(self, compiled: Compiled, values: dict[str, Any]):
        self.data = compiled.data
        self.env: dict[str, Any] = {k: c["value"] for k, c in self.data["constants"].items()}
        self.env.update(values)
        self.lookup_hits: dict[str, Any] = {}

    def value(self, name: str) -> Any:
        if name not in self.env:
            raise FormulaError(f"Unknown name {name}")
        value = self.env[name]
        if value is None or value == "":
            raise MissingValue(f"Missing value for {name}")
        return value

    def eval(self, node: AST) -> Any:
        tag = node[0]
        if tag in {"num", "str"}:
            return node[1]
        if tag == "var":
            return self.value(node[1])
        if tag == "un":
            operand = self.eval(node[2])
            if node[1] == "not":
                return 0.0 if _truthy(operand) else 1.0
            number = _num(operand, "operand")
            return -number if node[1] == "-" else number
        if tag == "bin":
            return self.binary(node[1], node[2], node[3])
        if tag == "call":
            return self.call(node[1], node[2])
        raise FormulaError(f"Bad AST node {tag}")

    def binary(self, op: str, left: AST, right: AST) -> Any:
        if op == "and":
            return 1.0 if _truthy(self.eval(left)) and _truthy(self.eval(right)) else 0.0
        if op == "or":
            return 1.0 if _truthy(self.eval(left)) or _truthy(self.eval(right)) else 0.0
        a, b = self.eval(left), self.eval(right)
        if op == "==":
            return 1.0 if _equal(a, b) else 0.0
        if op == "!=":
            return 0.0 if _equal(a, b) else 1.0
        x, y = _num(a, "left operand"), _num(b, "right operand")
        if op in {"/", "%"} and y == 0:
            raise FormulaError("Division by zero")
        try:
            result = {
                "+": lambda: x + y, "-": lambda: x - y, "*": lambda: x * y, "/": lambda: x / y,
                "%": lambda: math.fmod(x, y), "^": lambda: x ** y,
                "<": lambda: float(x < y), "<=": lambda: float(x <= y),
                ">": lambda: float(x > y), ">=": lambda: float(x >= y),
            }[op]()
        except (OverflowError, ZeroDivisionError) as error:
            raise FormulaError(f"Non-finite result for {op}") from error
        if isinstance(result, complex) or not math.isfinite(result):
            raise FormulaError(f"Non-finite result for {op}")
        return result

    def call(self, name: str, args: list[AST]) -> Any:
        if name == "if":
            return self.eval(args[1]) if _truthy(self.eval(args[0])) else self.eval(args[2])
        if name == "coalesce":
            for arg in args:
                try:
                    return self.eval(arg)
                except MissingValue:
                    continue
            raise MissingValue("coalesce(): every argument is missing")
        if name == "present":
            try:
                self.eval(args[0])
                return 1.0
            except MissingValue:
                return 0.0
        if name == "lookup":
            table_name = args[0][1]
            table = self.data["lookups"][table_name]
            key = lookup_key(self.eval(args[1]), table["key_type"])
            if key in table["index"]:
                entry = table["entries"][table["index"][key]]
                self.lookup_hits[table_name] = {"key": key, **entry}
                return entry["value"]
            if table["default"] is not None:
                self.lookup_hits[table_name] = {"key": key, "value": table["default"], "label": "default"}
                return table["default"]
            raise MissingValue(f"No {table_name} entry for key {key!r}")
        values = [_num(self.eval(a), f"{name}() argument") for a in args]
        if name == "min":
            return min(values)
        if name == "max":
            return max(values)
        if name == "round":
            return round_half_away(values[0], int(values[1]) if len(values) > 1 else 0)
        if name == "floor":
            return float(math.floor(values[0]))
        if name == "ceil":
            return float(math.ceil(values[0]))
        if name == "abs":
            return abs(values[0])
        raise FormulaError(f"Unknown function {name}")


def evaluate(compiled: Compiled, values: dict[str, Any]) -> dict:
    """Evaluate a compiled formula with input values. Never raises for data problems."""
    evaluator = Evaluator(compiled, values)
    data = compiled.data
    result: dict[str, Any] = {"formula": data["id"], "inputs": values, "terms": {}}
    try:
        if data["applies_ast"] is not None and not _truthy(evaluator.eval(data["applies_ast"])):
            result.update(applicable=False, value=0.0)
            return result
        for term in data["terms"]:
            try:
                evaluator.env[term["name"]] = evaluator.eval(term["ast"])
            except MissingValue:
                evaluator.env[term["name"]] = None
            result["terms"][term["name"]] = evaluator.env[term["name"]]
        value = _num(evaluator.eval(data["ast"]), "result")
        result.update(applicable=True, value=value)
    except MissingValue as error:
        result.update(applicable=True, value=None, missing=str(error))
    except FormulaError as error:
        result.update(applicable=True, value=None, error=str(error))
    if evaluator.lookup_hits:
        result["lookups"] = evaluator.lookup_hits
    return result
