// Guard for the front-end split: every name the markup calls and every id the
// script reaches for must actually exist after the files were separated.
//
// This is the failure mode of that split - a global that quietly stops resolving -
// and it needs no browser to catch: the inline handlers are visible in the HTML,
// and the function declarations are visible in the JS.
//
//   node tests/check_ui.mjs   (run from the project root)
import { readFileSync } from 'node:fs';

const html = readFileSync('web/index.html', 'utf8');
const js = readFileSync('web/app.js', 'utf8');
const problems = [];

// 1. Something is still inline: the point of the split.
if (/<style>/.test(html)) problems.push('index.html still has an inline <style> block');
if (/<script>/.test(html)) problems.push('index.html still has an inline <script> block');
if (!/<script src="\/static\/app\.js"><\/script>/.test(html)) {
  problems.push('index.html does not load /static/app.js');
}
if (!/<link rel="stylesheet" href="\/static\/style\.css">/.test(html)) {
  problems.push('index.html does not load /static/style.css');
}

// 2. Every function named by an inline event attribute must be declared in app.js
//    as a global (function declaration), not as a const arrow.
const handlers = new Set();
for (const m of html.matchAll(/\bon(?:click|change|input|submit|keyup|blur|focus)="([A-Za-z_$][\w$]*)\(/g)) {
  handlers.add(m[1]);
}
for (const fn of [...handlers].sort()) {
  // A top-level `function foo()` and a top-level `const foo = ...` are both
  // reachable from an inline handler: in a classic script both create global
  // bindings (var-style and lexical respectively) and the handler resolves names
  // through the global scope. What must NOT happen is the declaration becoming
  // indented, i.e. scoped inside something else - that is the failure this catches.
  const asFunction = new RegExp(`(^|\\n)(async\\s+)?function\\s+${fn}\\s*\\(`).test(js);
  const asBinding = new RegExp(`(^|\\n)(const|let|var)\\s+${fn}\\s*=`).test(js);
  if (!asFunction && !asBinding) {
    problems.push(`inline handler ${fn}() has no top-level declaration in app.js`);
  }
}

// 3. Every id the script looks up must exist - in the markup, or in a template
//    string the script itself injects.
const ids = new Set();
for (const m of js.matchAll(/\$\('#([\w-]+)'\)/g)) ids.add(m[1]);
const idsInHtml = new Set([...html.matchAll(/\bid="([\w-]+)"/g)].map(m => m[1]));
const idsInJs = new Set([...js.matchAll(/\bid="([\w-]+)"/g)].map(m => m[1]));
for (const id of [...ids].sort()) {
  if (!idsInHtml.has(id) && !idsInJs.has(id)) {
    problems.push(`#${id} is looked up by app.js but defined nowhere`);
  }
}

console.log(`inline handlers: ${handlers.size}, ids looked up: ${ids.size}`);
if (problems.length) {
  console.error('\n' + problems.join('\n'));
  process.exit(1);
}
console.log('front-end split OK');