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
