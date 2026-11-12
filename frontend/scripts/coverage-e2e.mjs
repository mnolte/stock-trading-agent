#!/usr/bin/env node
// Item 088: turn the raw Chromium V8 coverage that e2e/shots.ts writes to
// coverage-e2e/raw/<project>-<worker>.jsonl into istanbul reports under
// coverage-e2e/<project>/ (coverage-final.json, coverage-summary.json,
// html). Run from CI after the Playwright run, with E2E_COVERAGE=1 having
// produced a dist/ build with sourcemaps.
//
// Usage: node scripts/coverage-e2e.mjs --project desktop
import { CoverageReport } from 'monocart-coverage-reports';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const frontendDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..',
);
const coverageDir = path.join(frontendDir, 'coverage-e2e');
const rawDir = path.join(coverageDir, 'raw');
const distDir = path.join(frontendDir, 'dist');

const projectIndex = process.argv.indexOf('--project');
const project =
  projectIndex >= 0 ? process.argv[projectIndex + 1] : process.env.E2E_PROJECT;
if (!project) {
  console.error('usage: coverage-e2e.mjs --project <name>');
  process.exit(2);
}

// V8 entries carry no source text; monocart drops entries without one.
// Resolve each script URL to the file the preview server served
// (dist/assets/*) — or the dev-server path (/src/*) — and attach the
// source plus its parsed sourcemap so generation needs no server.
const assetCache = new Map();
function localAsset(url) {
  if (assetCache.has(url)) return assetCache.get(url);
  const asset = { source: undefined, sourceMap: undefined };
  try {
    const { pathname } = new URL(url);
    const filePath = pathname.startsWith('/assets/')
      ? path.join(distDir, pathname)
      : path.join(frontendDir, pathname);
    if (fs.existsSync(filePath)) {
      const source = fs.readFileSync(filePath, 'utf8');
      asset.source = source;
      const mapMatch = source.match(/\/\/# sourceMappingURL=(\S+)\s*$/);
      if (mapMatch) {
        const ref = mapMatch[1];
        if (ref.startsWith('data:')) {
          const b64 = ref.slice(ref.indexOf(',') + 1);
          asset.sourceMap = JSON.parse(
            Buffer.from(b64, 'base64').toString('utf8'),
          );
        } else {
          const mapPath = path.resolve(path.dirname(filePath), ref);
          if (fs.existsSync(mapPath)) {
            asset.sourceMap = JSON.parse(fs.readFileSync(mapPath, 'utf8'));
          }
        }
      }
    }
  } catch {
    // Unresolvable URL — the entry is filtered out below anyway.
  }
  assetCache.set(url, asset);
  return asset;
}

const entries = [];
if (fs.existsSync(rawDir)) {
  for (const name of fs.readdirSync(rawDir)) {
    if (!name.startsWith(`${project}-`) || !name.endsWith('.jsonl')) continue;
    for (const line of fs.readFileSync(path.join(rawDir, name), 'utf8').split('\n')) {
      if (!line.trim()) continue;
      const record = JSON.parse(line);
      for (const entry of record.entries ?? []) {
        if (typeof entry?.url === 'string' && entry.source === undefined) {
          const asset = localAsset(entry.url);
          entry.source = asset.source;
          entry.sourceMap = asset.sourceMap;
        }
        entries.push(entry);
      }
    }
  }
}

if (entries.length === 0) {
  console.log(`coverage-e2e: no V8 entries for project "${project}" — skipping`);
  process.exit(0);
}

const report = new CoverageReport({
  name: `e2e ${project}`,
  reports: [['json'], ['json-summary'], ['html']],
  outputDir: path.join(coverageDir, project),
  baseDir: frontendDir,
  // Only the app bundle is interesting; its sourcemap unpacks to src/**.
  entryFilter: (entry) => {
    try {
      return new URL(entry.url).pathname.startsWith('/assets/');
    } catch {
      return false;
    }
  },
  sourceFilter: '**/src/**',
  outputFilter: '**/src/**',
});
await report.add(entries);
await report.generate();
console.log(`coverage-e2e: wrote ${path.join(coverageDir, project)}`);
