#!/usr/bin/env node
/**
 * ATSFlow CLI Bridge — Runs the 30-rule ATS scanner from the command line.
 *
 * The scanner engine (ATSScanner, FormattingChecks, etc.) was designed for
 * browser <script> tag loading with global variables. This bridge properly
 * requires all modules for Node.js usage.
 *
 * Usage: node cli-analyze.js <resume.txt> [--format json|text]
 */

const fs = require('fs');
const path = require('path');

// ── Bootstrap globals that the scanner modules reference ────────────────
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.logger = require('../utils/logger.js');
global.logger.setLevel('error');
global.FormattingChecks = require('./checks/formatting.js');
global.StructureChecks = require('./checks/structure.js');
global.ContentChecks = require('./checks/content.js');
global.atsScorer = require('./scorer.js');
global.recommendationsEngine = require('./recommendations.js');

const scanner = require('./ats-scanner.js');

// ── Parse args ──────────────────────────────────────────────────────────
const args = process.argv.slice(2);
if (args.length === 0 || args[0] === '--help' || args[0] === '-h') {
  console.log('Usage: node cli-analyze.js <resume.txt> [--format json|text]');
  console.log('Runs the 30-rule ATS compatibility scan against a plain-text resume.');
  console.log('Output: JSON (default) or text summary.');
  process.exit(args.length === 0 ? 1 : 0);
}

const filePath = path.resolve(args[0]);
const formatArgIdx = args.indexOf('--format');
const format = formatArgIdx !== -1 ? args[formatArgIdx + 1] : 'json';

if (!fs.existsSync(filePath)) {
  console.error(`File not found: ${filePath}`);
  process.exit(1);
}

// ── Build resumeData from plain text ────────────────────────────────────
const rawText = fs.readFileSync(filePath, 'utf-8');
const lines = rawText.split('\n').map(l => l.trim());

// Crude section detection: split by lines that look like headers
const sections = [];
let currentSection = null;
let currentContent = [];

const SECTION_KEYWORDS = [
  'summary', 'objective', 'profile', 'about',
  'experience', 'work', 'employment', 'career',
  'education', 'degree', 'university', 'college', 'academic',
  'skills', 'technologies', 'competencies', 'proficiencies',
  'certifications', 'licenses', 'credentials',
  'projects', 'portfolio',
  'awards', 'honors', 'achievements',
  'volunteer', 'leadership', 'languages', 'references',
];

function looksLikeHeader(line) {
  const cleaned = line.replace(/[^a-zA-Z\s]/g, '').trim().toLowerCase();
  if (cleaned.length < 3 || cleaned.length > 40) return false;
  // Heuristic: line is ALL CAPS or has few words and matches section keywords
  if (line === line.toUpperCase() && line.length > 3) return true;
  const words = cleaned.split(/\s+/);
  if (words.length <= 3 && SECTION_KEYWORDS.some(k => cleaned.includes(k))) return true;
  return false;
}

for (const line of lines) {
  if (!line) continue;
  if (looksLikeHeader(line)) {
    if (currentSection) {
      sections.push({ type: currentSection.type, title: currentSection.title, content: { items: currentContent } });
    }
    currentSection = { type: line.toLowerCase(), title: line };
    currentContent = [];
  } else if (currentSection) {
    currentContent.push(line);
  }
}
if (currentSection && currentContent.length > 0) {
  sections.push({ type: currentSection.type, title: currentSection.title, content: { items: currentContent } });
}

// If no sections detected, put everything in one
if (sections.length === 0) {
  sections.push({ type: 'body', title: 'Resume', content: { items: lines.filter(Boolean) } });
}

const resumeData = {
  sections,
  rawText,
  metadata: { wordCount: rawText.split(/\s+/).filter(Boolean).length },
};

// ── Run scan ────────────────────────────────────────────────────────────
async function main() {
  const result = await scanner.scan(resumeData, {
    fileFormat: 'txt',
    industry: 'software',
  });

  if (format === 'text') {
    // Text summary
    const s = result.score;
    console.log(`ATSFlow Scan: ${s.overallScore}/100 (Grade: ${s.grade})`);
    console.log(`Checks: ${result.checks.passed}/${result.checks.total} passed, ${result.checks.failed} failed`);
    console.log(`\nCategory Scores:`);
    if (s.categoryScores) {
      for (const [cat, score] of Object.entries(s.categoryScores)) {
        console.log(`  ${cat}: ${score}`);
      }
    }
    console.log(`\nFailed Checks:`);
    for (const r of result.checks.results) {
      if (!r.passed) {
        console.log(`  [${r.severity || '?'}] ${r.checkName}: ${r.message}`);
        if (r.recommendation) console.log(`    → ${r.recommendation}`);
      }
    }
    if (result.recommendations?.quickWins?.length) {
      console.log(`\nQuick Wins (high impact, low effort):`);
      for (const w of result.recommendations.quickWins) {
        console.log(`  ${w.rank}. ${w.issue}`);
      }
    }
  } else {
    console.log(JSON.stringify(result, null, 2));
  }
}

main().catch(err => {
  console.error('Scan error:', err.message);
  process.exit(1);
});
