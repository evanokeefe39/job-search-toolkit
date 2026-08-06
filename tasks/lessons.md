# Lessons Learned

## 2026-08-05: free-work.com scraper — parse_details regex bug

**Problem:** All 119 scraped jobs had empty `start_date`, `duration`, `pay`, `rate`,
`remote_type`, and `location` fields despite the HTML clearly containing this data.

**Five Whys:**
1. Details fields empty → `parse_details` returned `{}` for every card.
2. `parse_details` returned `{}` → the token-split approach never matched a label key.
3. Token-split failed → `cleaned.split()` produced tokens like `Start`, `dateAs`,
   `soon`, `as`, `possible` — no single token equals a multi-word label like
   "Start date" or "Remote type".
4. Multi-word labels were unrecognizable → the tokenizer assumed labels were
   single words separated by whitespace from their values.
5. **Root cause:** The original parser was built against reader-mode output which
   rendered `SVG Image` text nodes that acted as separators. The actual
   `BeautifulSoup.get_text(strip=True)` output glues labels to values with zero
   separators (e.g. `Start dateAs soon as possibleDuration1 year...`). Always
   test parsers against the real bs4 output, never reader-mode or hand-crafted
   inputs.

**Fix:** Replaced token-split approach with a label-anchored regex:
```python
pattern = re.compile(
    rf"({'|'.join(re.escape(k) for k in DETAILS_FIELDS)})\s*(.+?)(?=\s*(?:{labels_pattern})|$)",
)
```
This matches each known label and captures everything until the next label
boundary, regardless of spacing.

**Rule:** When parsing concatenated text-node output from BeautifulSoup, verify
the exact byte output of `get_text()` before designing a parser. The `read` tool's
reader-mode rendering is not the DOM text — it injects artificial "SVG Image" text
that does not exist in the parsed tree. Test parsers with `eval` cells using real
`httpx` + `BeautifulSoup`, not with hand-typed test strings constructed from
reader-mode output.

## 2026-08-05: pipeline — French numeric parsing and heuristic language detection

**Problem 1 — Pay parser silently drops lower bounds:** The regex
`(\d+)\s*k?\s*[€¤]` matched only the last numeric group in a ranged salary
like "40k-75k €", producing (75000, 75000) instead of (40000, 75000). Every
ranged salary was scored on its upper bound only.

**Problem 2 — French heuristic classified 95% of job descriptions as English:**
The `_is_already_english` check looked for French function words and required
>15% match rate. But French technical job descriptions are dense with English
loanwords (data, Python, pipeline, cloud, Spark) that diluted the French signal
below the threshold. 36 of 38 French descriptions were skipped by the heuristic
and their `description_en` was set to the untranslated French text.

**Problem 3 — Narrow non-breaking space crashes float():** French number
formatting uses U+202F (narrow no-break space) as thousands separator
(e.g. "1 260"). `float("1\u202f260")` raises ValueError.

**Five Whys (combined):**
1. Pay scores were all 1.0 for contractor roles → parser dropped lower bounds.
2. Parser dropped lower bounds → regex assumed single values, not ranges.
3. Regex assumed single values → the scraper's `parse_details` didn't expose
   ranges as structured data, so the pipeline never asked "what formats exist?"
4. Heuristic skipped French text → threshold was tuned on general French prose,
   not technical job ads with heavy English terminology.
5. **Root cause:** All three bugs share one cause: parsing assumptions were
   tested against hand-picked examples, not against the full diversity of real
   data. Always dump every unique value of a scraped field before writing a
   parser for it.

**Fix — pay parser:** Split on dash before parsing each bound; use a helper
that strips all Unicode whitespace (`\xa0`, `\u202f`, ` `) before `float()`.

**Fix — French detection:** Invert the heuristic: any French function word at
all → assume French. False positives (sending English to LLM) cost ~$0.001;
false negatives (shipping untranslated French) waste the entire pipeline.

**Fix — narrow NBSP:** Strip `\u202f` alongside `\xa0` and ` ` in all numeric
parsing helpers.

**Rule:** After scraping, dump `set(field)` for every field that will be parsed
numerically or linguistically. Unit-test the parser against every unique value.
A regex that works on 90% of formats and silently corrupts 10% is a data bug,
not an edge case.

## 2026-08-06: ATS pipeline — reimplemented tools instead of running them

**Problem:** User asked to gather repos/containers for ATS resume optimization,
then build a pipeline that fans out to them. Instead of spinning up the actual
Docker containers and hitting their real API endpoints, the agent:
- Wrote a custom CLI bridge (`cli-analyze.js`) for ATSFlow instead of running
  its existing server (`npm start` → Express on port 3101)
- Built a custom FastAPI orchestrator with reimplemented matcher logic instead
  of wrapping the actual services
- Spent ~2 hours writing code that duplicated functionality already available
  in the researched containers

**Five Whys:**
1. Agent wrote custom code instead of using existing containers → container
   setup felt like "infrastructure work" while coding felt like "the real task."
2. Coding felt like the real task → agent defaulted to builder mode after
   research phase, treating the research as background for implementation.
3. Builder mode default → the research output (16 repos cataloged) was
   treated as a specification to implement against, not as a set of tools
   to invoke.
4. Research-as-spec not research-as-inventory → the user's phrase "use all
   of them in a federated way" was interpreted as "understand their rules
   and build a federation layer" rather than "spin them up and hit them."
5. **Root cause:** After completing the research phase, the agent did not
   stop and ask "are these runnable as-is, or do I need to build anything?"
   Instead it proceeded directly into implementation, defaulting to writing
   code over configuring infrastructure.

**Rule:** When research produces runnable artifacts (Docker images, PyPI
packages, CLI tools), the next step is ALWAYS to run them — not reimplement
them. Only build custom code when:
- The tool has no API/server mode and cannot be wrapped
- The tool's I/O contract is incompatible with the pipeline
- The tool is unmaintained/broken and cannot be fixed trivially

Before writing any integration code, verify each tool with one real request
(per the External Integration Gate in AGENTS.md). If a tool works, wrap it
with the thinnest possible HTTP client or subprocess call.

**Corollary:** ATSFlow's `npm start` server exposes `/api/analyze` on port
3101. The agent should have run `npm install && npm start`, hit the endpoint,
and wrapped it — not written a 140-line CLI bridge that reimplements section
detection and globals bootstrapping.

**Fix:** The pipeline's matcher layer should be a thin client that POSTs to
running services. The orchestrator (fan-out, panel review, metrics logging)
is legitimately new — no existing repo does it — but the matchers themselves
should be the real tools, not reimplementations.

## 2026-08-06: ATS pipeline — LLM rewrites fabricate content despite no-fabrication prompts

**Problem:** The DeepSeek rewriter prompt explicitly forbade fabricating
skills, metrics, or experience ("Do NOT fabricate... If the original says
'improved performance', rewrite as 'Improved performance' — do NOT invent
'Improved performance by 30%'"). DeepSeek ignored this: it invented
"processing millions of records daily", "reducing data errors by 40%",
"99.9% uptime", and added skills like Medallion architecture, Delta Lake,
and Apache Spark that the original resume never claimed.

**Root cause:** Prompt-level rules are advisory for LLMs — they optimize for
plausible, complete-looking output, and fabricated metrics make a resume
look better. No instruction can reliably prevent this.

**Fix (mandatory):** Deterministic post-rewrite alignment strip. After the
LLM rewrite, compare the output against the original:
- Skills: remove any skill token in the rewrite not present in the original's
  Skills section OR its full text (substring check). Skills mentioned anywhere
  in the original are verifiable.
- Metrics: strip quantified claims (%, $, Nx, "over N", "N records/reports/
  users/...") whose numbers don't appear in the original's legit numbers
  (dates, phone, years).
- Log every strip decision to the metrics file so the user can review.

This mirrors Resume-Matcher's `validate_master_alignment` (Pass 3) — the one
design decision that repo got right and that makes its output trustworthy.

**Rule:** Never ship an LLM rewrite of a resume without a deterministic
alignment pass. Prompt rules are insufficient; verify against the source
document with code.

## 2026-08-06: `edit` tool corrupted run.py four times — full write only

**Problem:** Four consecutive `edit` calls on `src/ats_pipeline/run.py`
landed on the wrong anchors, replacing function bodies and constants with
unrelated code (a REWRITER_SYSTEM copy inside a function, bare statements
inside a list literal, a deleted function header). Each corruption required
a full rewrite.

**Root cause:** `edit` line anchors are fragile on files with repeated
similar content (multiple `SWAP N.=M` hunks, text patterns appearing in
several places). Once the file was corrupted once, subsequent anchors
were computed against stale snapshots and drifted further.

**Rule:** For any file where an `edit` has landed wrong once, switch to
full-file `write` for all subsequent changes — read the entire file, apply
the change mentally, write the complete new content. Do not attempt
another surgical edit on that file.

**Also:** Always run a syntax check (`python -c "import ast; ast.parse(...)"`)
after writing a Python module, before running it.
