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
