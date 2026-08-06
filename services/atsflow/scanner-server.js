#!/usr/bin/env node
/**
 * ATSFlow Scanner API — exposes the 30-rule ATS scanner over REST.
 * Thin wrapper around ATSScanner. No Claude/LLM dependency.
 */

const express = require('express');
const fs = require('fs');
const path = require('path');
const { tmpdir } = require('os');

// Bootstrap globals the scanner modules expect (browser-style)
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
global.logger = require('./js/utils/logger.js');
global.logger.setLevel('error');
global.FormattingChecks = require('./js/analyzer/checks/formatting.js');
global.StructureChecks = require('./js/analyzer/checks/structure.js');
global.ContentChecks = require('./js/analyzer/checks/content.js');
global.atsScorer = require('./js/analyzer/scorer.js');
global.recommendationsEngine = require('./js/analyzer/recommendations.js');

const scanner = require('./js/analyzer/ats-scanner.js');

// Section keywords for plain-text parsing
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
  if (line === line.toUpperCase() && line.length > 3) return true;
  const words = cleaned.split(/\s+/);
  return words.length <= 3 && SECTION_KEYWORDS.some(k => cleaned.includes(k));
}

function buildResumeData(text) {
  const lines = text.split('\n').map(l => l.trim());
  const sections = [];
  let currentSection = null;
  let currentContent = [];

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
  if (sections.length === 0) {
    sections.push({ type: 'body', title: 'Resume', content: { items: lines.filter(Boolean) } });
  }

  return { sections, rawText: text, metadata: { wordCount: text.split(/\s+/).filter(Boolean).length } };
}

// ── Express app ─────────────────────────────────────────────────────
const app = express();
app.use(express.json({ limit: '1mb' }));

app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'atsflow-scanner', version: scanner.version });
});

app.post('/analyze', async (req, res) => {
  const { resume_text, job_description } = req.body;
  if (!resume_text) {
    return res.status(400).json({ error: 'resume_text is required' });
  }

  try {
    const resumeData = buildResumeData(resume_text);
    const result = await scanner.scan(resumeData, { fileFormat: 'txt', industry: 'software' });

    // Return a clean subset: score + failed checks + recommendations
    const failed = result.checks.results.filter(r => !r.passed);
    res.json({
      service: 'atsflow-scanner',
      method: '30-rule-compliance',
      score: result.score.overallScore,
      grade: result.score.grade,
      checks_passed: result.checks.passed,
      checks_failed: result.checks.failed,
      category_scores: Object.fromEntries(
        Object.entries(result.score.categoryScores).map(([k, v]) => [k, v.score])
      ),
      issues: failed.map(r => ({
        check: r.checkName,
        category: r.category,
        severity: r.severity,
        message: r.message,
        recommendation: r.recommendation || '',
        impact: r.impact || 'medium',
      })),
      recommendations: (result.recommendations?.allRecommendations || []).map(r =>
        `[${r.impact || '?'}] ${r.checkName}: ${r.recommendation || r.issue}`
      ),
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

const PORT = process.env.PORT || 3101;
app.listen(PORT, () => {
  console.log(`ATSFlow scanner API on port ${PORT}`);
});
