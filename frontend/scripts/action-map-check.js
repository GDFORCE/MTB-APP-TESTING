#!/usr/bin/env node
/**
 * Action-map audit — cross-app P0 gate.
 *
 * Statically verifies that EVERY pressable control in the app source
 * (`Pressable`, `TouchableOpacity`, `Springy`) declares a real action:
 *   • the opening tag must carry onPress / onLongPress / an accessible
 *     `disabled` binding, or spread its props from the call site;
 *   • literal no-op handlers (`onPress={() => {}}`, `onPress={undefined}`,
 *     `onPress={null}`) are rejected anywhere in the tree;
 *   • "coming soon"-style placeholder strings are rejected in app source.
 *
 * Runs with plain Node (no test framework needed):  node scripts/action-map-check.js
 * Exits non-zero with a file:line report when a violation is found.
 */
const fs = require("fs");
const path = require("path");

const ROOTS = ["app", "src"];
const PRESSABLE_TAGS = ["Pressable", "TouchableOpacity", "Springy"];
const NOOP_PATTERNS = [
  /onPress=\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}/, // onPress={() => {}}
  /onPress=\{\s*undefined\s*\}/,
  /onPress=\{\s*null\s*\}/,
  /onLongPress=\{\s*\(\s*\)\s*=>\s*\{\s*\}\s*\}/,
];
const PLACEHOLDER_PATTERNS = [
  /coming soon/i,
  /not yet implemented/i,
];

function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name.startsWith(".")) continue;
      walk(full, out);
    } else if (/\.(tsx|ts)$/.test(entry.name) && !/\.d\.ts$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

function lineOf(source, index) {
  return source.slice(0, index).split("\n").length;
}

/** Extract the full opening tag starting at `start` (index of "<"). */
function openingTag(source, start) {
  let depth = 0;
  for (let i = start; i < source.length; i++) {
    const ch = source[i];
    if (ch === "{") depth++;
    else if (ch === "}") depth--;
    else if (ch === ">" && depth === 0) return source.slice(start, i + 1);
  }
  return source.slice(start, start + 400);
}

const violations = [];
const files = ROOTS.flatMap((root) =>
  fs.existsSync(root) ? walk(root) : []);

for (const file of files) {
  const source = fs.readFileSync(file, "utf8");

  for (const pattern of NOOP_PATTERNS) {
    const match = source.match(pattern);
    if (match) {
      violations.push(`${file}:${lineOf(source, match.index)} — literal no-op handler: ${match[0]}`);
    }
  }
  for (const pattern of PLACEHOLDER_PATTERNS) {
    const match = source.match(pattern);
    if (match) {
      violations.push(`${file}:${lineOf(source, match.index)} — placeholder copy: "${match[0]}"`);
    }
  }

  for (const tag of PRESSABLE_TAGS) {
    const re = new RegExp(`<${tag}(?=[\\s/>])`, "g");
    let m;
    while ((m = re.exec(source)) !== null) {
      const tagSource = openingTag(source, m.index);
      const hasAction =
        /onPress\s*=/.test(tagSource) ||
        /onLongPress\s*=/.test(tagSource) ||
        /onPressIn\s*=/.test(tagSource) ||
        /\{\.\.\./.test(tagSource); // prop spread — handler supplied by caller
      if (!hasAction) {
        violations.push(`${file}:${lineOf(source, m.index)} — <${tag}> without onPress/onLongPress/spread`);
      }
    }
  }
}

if (violations.length > 0) {
  console.error(`Action-map audit FAILED — ${violations.length} violation(s):\n`);
  for (const v of violations) console.error("  " + v);
  process.exit(1);
}
console.log(`Action-map audit passed — ${files.length} files scanned, every pressable declares an action.`);
