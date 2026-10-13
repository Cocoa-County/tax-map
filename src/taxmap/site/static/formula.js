/* Assessment formula evaluator. Mirrors taxmap/formula/__init__.py (Evaluator) exactly:
 * same AST, number parsing, equality, truthiness, rounding, and missing-value rules.
 * ASTs are produced by the Python compiler and shipped in data/formulas.json. */
(function (root) {
  "use strict";

  class FormulaError extends Error {}
  class MissingValue extends FormulaError {}

  const NUMBER_TEXT = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/;

  function toNumber(value) {
    if (value === null || value === undefined) return null;
    if (typeof value === "boolean") return value ? 1 : 0;
    if (typeof value === "number") return Number.isFinite(value) ? value : null;
    const text = String(value).trim().replace(/,/g, "").replace(/\$/g, "");
    return NUMBER_TEXT.test(text) ? Number(text) : null;
  }

  function textValue(value) {
    if (value === null || value === undefined) return "";
    if (typeof value === "boolean") return value ? "1" : "0";
    if (typeof value === "number") {
      if (Number.isInteger(value) && Math.abs(value) < 1e15) return String(value);
      return pyFloatRepr(value);
    }
    return String(value).trim();
  }

  // Python repr for non-integral floats matches JS String() except exponent formatting.
  function pyFloatRepr(value) {
    const text = String(value);
    const match = /^(-?[\d.]+)e([+-])(\d+)$/.exec(text);
    if (!match) return text;
    return `${match[1]}e${match[2]}${match[3].padStart(2, "0")}`;
  }

  function lookupKey(value, keyType) {
    if (keyType === "int") {
      const number = toNumber(value);
      if (number === null || number !== Math.trunc(number)) return textValue(value).trim();
      return String(Math.trunc(number));
    }
    return textValue(value).trim();
  }

  function num(value, what) {
    if (typeof value === "boolean") return value ? 1 : 0;
    if (typeof value === "number") return value;
    const number = toNumber(value);
    if (number === null) throw new FormulaError(`${what} is not a number: ${JSON.stringify(value)}`);
    return number;
  }

  function truthy(value) {
    if (typeof value === "string") return value !== "";
    if (typeof value === "number") return value !== 0 && !Number.isNaN(value);
    return Boolean(value);
  }

  function equal(a, b) {
    if (typeof a === "number" || typeof b === "number") {
      const na = toNumber(a), nb = toNumber(b);
      if (na !== null && nb !== null) return na === nb;
    }
    return textValue(a) === textValue(b);
  }

  function roundHalfAway(value, digits) {
    const factor = Math.pow(10, digits || 0);
    const magnitude = Math.floor(Math.abs(value) * factor + 0.5 + 1e-9) / factor;
    return value < 0 || Object.is(value, -0) ? -magnitude : magnitude;
  }

  function fmod(x, y) {
    return x % y;
  }

  class Evaluator {
    constructor(data, values) {
      this.data = data;
      this.env = {};
      for (const [name, constant] of Object.entries(data.constants || {})) this.env[name] = constant.value;
      Object.assign(this.env, values || {});
      this.lookupHits = {};
    }

    value(name) {
      if (!(name in this.env)) throw new FormulaError(`Unknown name ${name}`);
      const value = this.env[name];
      if (value === null || value === undefined || value === "") throw new MissingValue(`Missing value for ${name}`);
      return value;
    }

    eval(node) {
      const tag = node[0];
      if (tag === "num" || tag === "str") return node[1];
      if (tag === "var") return this.value(node[1]);
      if (tag === "un") {
        const operand = this.eval(node[2]);
        if (node[1] === "not") return truthy(operand) ? 0 : 1;
        const number = num(operand, "operand");
        return node[1] === "-" ? -number : number;
      }
      if (tag === "bin") return this.binary(node[1], node[2], node[3]);
      if (tag === "call") return this.call(node[1], node[2]);
      throw new FormulaError(`Bad AST node ${tag}`);
    }

    binary(op, left, right) {
      if (op === "and") return truthy(this.eval(left)) && truthy(this.eval(right)) ? 1 : 0;
      if (op === "or") return truthy(this.eval(left)) || truthy(this.eval(right)) ? 1 : 0;
      const a = this.eval(left), b = this.eval(right);
      if (op === "==") return equal(a, b) ? 1 : 0;
      if (op === "!=") return equal(a, b) ? 0 : 1;
      const x = num(a, "left operand"), y = num(b, "right operand");
      if ((op === "/" || op === "%") && y === 0) throw new FormulaError("Division by zero");
      const result = {
        "+": () => x + y, "-": () => x - y, "*": () => x * y, "/": () => x / y,
        "%": () => fmod(x, y), "^": () => Math.pow(x, y),
        "<": () => Number(x < y), "<=": () => Number(x <= y), ">": () => Number(x > y), ">=": () => Number(x >= y),
      }[op]();
      if (!Number.isFinite(result)) throw new FormulaError(`Non-finite result for ${op}`);
      return result;
    }

    call(name, args) {
      if (name === "if") return truthy(this.eval(args[0])) ? this.eval(args[1]) : this.eval(args[2]);
      if (name === "coalesce") {
        for (const arg of args) {
          try { return this.eval(arg); } catch (error) { if (!(error instanceof MissingValue)) throw error; }
        }
        throw new MissingValue("coalesce(): every argument is missing");
      }
      if (name === "present") {
        try { this.eval(args[0]); return 1; } catch (error) { if (error instanceof MissingValue) return 0; throw error; }
      }
      if (name === "lookup") {
        const tableName = args[0][1];
        const table = this.data.lookups[tableName];
        const key = lookupKey(this.eval(args[1]), table.key_type);
        if (Object.prototype.hasOwnProperty.call(table.index, key)) {
          const entry = table.entries[table.index[key]];
          this.lookupHits[tableName] = Object.assign({ key }, entry);
          return entry.value;
        }
        if (table.default !== null && table.default !== undefined) {
          this.lookupHits[tableName] = { key, value: table.default, label: "default" };
          return table.default;
        }
        throw new MissingValue(`No ${tableName} entry for key ${JSON.stringify(key)}`);
      }
      const values = args.map((a) => num(this.eval(a), `${name}() argument`));
      switch (name) {
        case "min": return Math.min(...values);
        case "max": return Math.max(...values);
        case "round": return roundHalfAway(values[0], values.length > 1 ? Math.trunc(values[1]) : 0);
        case "floor": return Math.floor(values[0]);
        case "ceil": return Math.ceil(values[0]);
        case "abs": return Math.abs(values[0]);
        default: throw new FormulaError(`Unknown function ${name}`);
      }
    }
  }

  /** Evaluate compiled formula data with input values. Never throws for data problems. */
  function evaluate(data, values) {
    const evaluator = new Evaluator(data, values);
    const result = { formula: data.id, inputs: values, terms: {} };
    try {
      if (data.applies_ast && !truthy(evaluator.eval(data.applies_ast))) {
        result.applicable = false;
        result.value = 0;
        return result;
      }
      for (const term of data.terms || []) {
        try {
          evaluator.env[term.name] = evaluator.eval(term.ast);
        } catch (error) {
          if (!(error instanceof MissingValue)) throw error;
          evaluator.env[term.name] = null;
        }
        result.terms[term.name] = evaluator.env[term.name];
      }
      result.applicable = true;
      result.value = num(evaluator.eval(data.ast), "result");
    } catch (error) {
      result.applicable = true;
      result.value = null;
      if (error instanceof MissingValue) result.missing = error.message;
      else if (error instanceof FormulaError) result.error = error.message;
      else throw error;
    }
    if (Object.keys(evaluator.lookupHits).length) result.lookups = evaluator.lookupHits;
    return result;
  }

  const api = { evaluate, toNumber, textValue, lookupKey, roundHalfAway, FormulaError, MissingValue };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TaxmapFormula = api;
})(typeof window !== "undefined" ? window : globalThis);
