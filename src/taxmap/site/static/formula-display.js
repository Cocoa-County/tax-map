(function (root) {
  "use strict";

  const escape = (value) => String(value).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tag = (name, content) => `<${name}>${content}</${name}>`;
  const row = (...items) => tag("mrow", items.join(""));
  const op = (value) => tag("mo", escape(value));
  const parens = (content) => row(op("("), content, op(")"));
  const number = (value) => Number.isFinite(value)
    ? value.toLocaleString("en-US", { maximumFractionDigits: 10 }) : String(value);

  function symbols(formula, values = {}, result = {}) {
    const entries = {};
    for (const input of formula.inputs) {
      entries[input.name] = { ...input, kind: "input", value: values[input.name] };
    }
    for (const [name, constant] of Object.entries(formula.constants)) {
      entries[name] = { ...constant, name, kind: "constant" };
    }
    for (const term of formula.terms) {
      entries[term.name] = { ...term, kind: "calculated", value: (result.terms || {})[term.name] };
    }
    for (const [name, table] of Object.entries(formula.lookups)) {
      entries[name] = { name, label: table.label || name, kind: "lookup" };
    }
    return entries;
  }

  function symbol(name) {
    const index = name.indexOf("_");
    return index > 0 && index < name.length - 1
      ? tag("msub", tag("mi", escape(name.slice(0, index))) + tag("mi", escape(name.slice(index + 1))))
      : tag("mi", escape(name));
  }

  function reference(name, entries, substitute) {
    const entry = entries[name];
    const value = entry && entry.value;
    const known = value !== null && value !== undefined && value !== "";
    const content = substitute && entry && entry.kind !== "lookup"
      ? (known ? tag(typeof value === "number" ? "mn" : "mtext", escape(number(value))) : tag("mtext", "?"))
      : symbol(name);
    if (!entry) return content;
    const description = [entry.label || name, entry.description, entry.unit,
      entry.kind === "lookup" ? "Lookup table" : `${known ? number(value) : "Not available"}`,
      entry.caveat].filter(Boolean).join(" — ");
    // HTML inside a MathML text integration point reliably emits keyboard focus events.
    return `<mtext><span xmlns="http://www.w3.org/1999/xhtml" class="formula-symbol symbol-${entry.kind}" tabindex="0" aria-label="${escape(description)}"><math xmlns="http://www.w3.org/1998/Math/MathML">${content}</math></span></mtext>`;
  }

  function expression(ast, entries, substitute = false, precedence = 0) {
    const render = (node, level = 0) => expression(node, entries, substitute, level);
    switch (ast[0]) {
      case "num": return tag("mn", escape(number(ast[1])));
      case "str": return tag("mtext", escape(`"${ast[1]}"`));
      case "var": return reference(ast[1], entries, substitute);
      case "un": {
        const level = ast[1] === "not" ? 3 : 7;
        const content = row(op(ast[1] === "not" ? "¬" : ast[1] === "-" ? "−" : ast[1]), render(ast[2], level));
        return level < precedence ? parens(content) : content;
      }
      case "bin": {
        if (ast[1] === "/") return tag("mfrac", row(render(ast[2])) + row(render(ast[3])));
        const level = { or: 1, and: 2, "==": 4, "!=": 4, "<": 4, ">": 4, "<=": 4, ">=": 4,
          "+": 5, "-": 5, "*": 6, "%": 6, "^": 8 }[ast[1]];
        const a = render(ast[2], ast[1] === "^" ? level + 1 : level);
        const b = render(ast[3], ast[1] === "^" ? 0 : level + 1);
        if (ast[1] === "^") {
          const content = tag("msup", row(a) + row(b));
          return level < precedence ? parens(content) : content;
        }
        const operators = { "*": "×", "-": "−", "==": "=", "!=": "≠", "<=": "≤", ">=": "≥", and: "∧", or: "∨", "%": "mod" };
        const content = row(a, op(operators[ast[1]] || ast[1]), b);
        return level < precedence ? parens(content) : content;
      }
      case "call": {
        const args = ast[2];
        if (ast[1] === "if") {
          return row(op("{"), tag("mtable",
            tag("mtr", tag("mtd", render(args[1])) + tag("mtd", row(tag("mtext", "if "), render(args[0])))) +
            tag("mtr", tag("mtd", render(args[2])) + tag("mtd", tag("mtext", "otherwise")))));
        }
        if (ast[1] === "abs") return row(op("|"), render(args[0]), op("|"));
        if (ast[1] === "floor" || ast[1] === "ceil") {
          return row(op(ast[1] === "floor" ? "⌊" : "⌈"), render(args[0]), op(ast[1] === "floor" ? "⌋" : "⌉"));
        }
        return row(tag("mi", escape(ast[1])), parens(row(...args.flatMap((arg, i) => i ? [op(","), render(arg)] : [render(arg)]))));
      }
      default: throw new Error(`Unknown formula AST node: ${ast[0]}`);
    }
  }

  function equation(ast, entries, { substitute = false, name, value, label = "Formula" } = {}) {
    const left = name ? reference(name, entries, false) : tag("mtext", "Assessment");
    let content = row(left, op("="), expression(ast, entries, substitute));
    if (substitute && value !== null && value !== undefined) {
      content = row(content, op("="), tag("mn", escape(number(value))));
    }
    return `<math xmlns="http://www.w3.org/1998/Math/MathML" display="block" aria-label="${escape(label)}">${content}</math>`;
  }

  const api = { symbols, equation, number };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.TaxmapFormulaDisplay = api;
})(typeof window !== "undefined" ? window : globalThis);
