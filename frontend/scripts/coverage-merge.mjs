#!/usr/bin/env node
// Item 088: merge the frontend's istanbul coverage maps into the three
// report variants — unit (Vitest), e2e (both Playwright projects) and all —
// under <out>/{unit,e2e,all}/ (coverage-final.json, coverage-summary.json,
// html). Inputs are directories holding a coverage-final.json; missing or
// unreadable inputs skip that variant instead of failing the job.
//
// Usage: node scripts/coverage-merge.mjs --unit DIR --e2e DIR [--e2e DIR] --out DIR
import { CoverageReport } from 'monocart-coverage-reports';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
);

function parseArgs(argv) {
  const args = { e2e: [], out: null };
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--unit') args.unit = argv[++i];
    else if (argv[i] === '--e2e') args.e2e.push(argv[++i]);
    else if (argv[i] === '--out') args.out = argv[++i];
  }
  return args;
}

const args = parseArgs(process.argv.slice(2));
if (!args.out) {
  console.error('usage: coverage-merge.mjs --unit DIR --e2e DIR [--e2e DIR] --out DIR');
  process.exit(2);
}
const outDir = path.resolve(args.out);

// Artifact downloads land the report files at varying depths; find the
// istanbul coverage-final.json inside each input directory.
function findFinalJson(dir) {
  if (!dir) return null;
  const direct = path.join(dir, 'coverage-final.json');
  if (fs.existsSync(direct)) return direct;
  const stack = [dir];
  while (stack.length) {
    const current = stack.pop();
    for (const entry of fs.readdirSync(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) stack.push(full);
      else if (entry.name === 'coverage-final.json') return full;
    }
  }
  return null;
}

// Normalize every file key to "src/..." so the unit (absolute paths from
// Vitest) and e2e (relative paths from monocart) maps line up.
function normalizeKeys(data) {
  const out = {};
  for (const [key, cov] of Object.entries(data)) {
    if (key === 'total' || typeof cov !== 'object' || cov === null) continue;
    const idx = key.indexOf('/src/');
    const rel = key.startsWith('src/')
      ? key
      : idx >= 0
        ? key.slice(idx + 1)
        : key.replace(/^\/+/, '');
    cov.path = rel;
    out[rel] = cov;
  }
  return out;
}

function loadVariant(dirs) {
  const files = [];
  for (const dir of dirs) {
    const found = findFinalJson(dir);
    if (!found) {
      console.log(`coverage-merge: no coverage-final.json under ${dir} — skipped`);
      continue;
    }
    try {
      files.push(normalizeKeys(JSON.parse(fs.readFileSync(found, 'utf8'))));
    } catch (error) {
      console.log(`coverage-merge: unreadable ${found}: ${error} — skipped`);
    }
  }
  return files.length ? files : null;
}

// istanbul merges hits index-wise; two converters can produce different
// statement maps for the same file, which would silently corrupt "all".
function mapsCompatible(a, b) {
  for (const [pathKey, covA] of Object.entries(a)) {
    const covB = b[pathKey];
    if (!covB) continue;
    if (
      JSON.stringify(covA.statementMap) !== JSON.stringify(covB.statementMap) ||
      JSON.stringify(covA.branchMap) !== JSON.stringify(covB.branchMap)
    ) {
      return false;
    }
  }
  return true;
}

async function generate(name, files) {
  const report = new CoverageReport({
    name: `coverage ${name}`,
    reports: [['json'], ['json-summary'], ['html']],
    outputDir: path.join(outDir, name),
    baseDir: frontendDir,
  });
  for (const file of files) {
    await report.add(file);
  }
  await report.generate();
  console.log(`coverage-merge: wrote ${path.join(outDir, name)}`);
}

const unit = args.unit ? loadVariant([args.unit]) : null;
const e2e = args.e2e.length ? loadVariant(args.e2e) : null;

if (unit) await generate('unit', unit);
if (e2e) await generate('e2e', e2e);
if (unit && e2e) {
  // Cheap compatibility probe: compare maps of shared paths across inputs.
  const allInputs = [...unit, ...e2e];
  let compatible = true;
  for (let i = 0; i < allInputs.length && compatible; i++) {
    for (let j = i + 1; j < allInputs.length && compatible; j++) {
      compatible = mapsCompatible(allInputs[i], allInputs[j]);
    }
  }
  if (compatible) {
    await generate('all', allInputs);
  } else {
    console.log(
      'coverage-merge: unit/e2e statement maps differ — ' +
        'skipping the "all" merge (shown side by side instead)',
    );
  }
}
