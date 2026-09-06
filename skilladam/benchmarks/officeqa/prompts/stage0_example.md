Below is a demonstration of the expected output format. The domain content
is illustrative only — do not transfer any of it into your output.

Input: 5 trajectory summaries from a Treasury Bulletin document QA task.

Output:

```json
{
  "metadata_json": {
    "name": "treasury-bulletin-qa",
    "description": "Answer questions using local Treasury Bulletin text files by searching, extracting evidence, computing, and formatting the result.",
    "when_to_use": [
      "Questions ask for facts, table values, chart readings, or calculations from U.S. Treasury Bulletin documents."
    ]
  },
  "skill_body_md": "# Treasury Bulletin QA\n\n## Workflow\n\n1. **Locate the relevant bulletin evidence**: Start from provided parsed pages and source hints; otherwise search by bulletin date, table or chart name, and requested year. If a parsed page is introductory or truncated, search same-bulletin nearby pages before broadening. Candidate tools: glob for filename patterns, grep for key phrases, read for page content.\n\n2. **Target the exact row, column, or chart mark**: Identify the requested period, entity, unit, and expected observation count before extracting values. For tables with multi-row headers, align values by exact row label and column header rather than proximity. Watch for adjacent columns representing different transaction roles (offered vs accepted, competitive vs noncompetitive) or different units (amounts vs percentages). For time-series requests, enumerate the expected count of observations and verify completeness. Candidate tools: targeted grep, small page reads.\n\n3. **Compute from extracted operands only**: Before computing, settle the formula convention the question requires (log vs simple growth, population vs sample variance, inclusive vs exclusive endpoints). Extract operands with their semantic roles explicit. For multi-step arithmetic, list every operand and intermediate result in reasoning. Normalize units after extraction and keep values unrounded until the final result. For 32nds quotation format, convert correctly (e.g., 99.27 means 99 + 27/32). Candidate tools: calculator or careful manual arithmetic.\n\n4. **Finalize in the requested format**: Return only the requested value. If the question asks for a rate, yield, or percent change, include '%'. If it asks for a plain numeric value, ratio, or count, omit '%' and unit words. Use ASCII minus for negatives. Candidate tools: none.\n\n## Error Avoidance\n\n- Do not substitute adjacent column values for the requested measure — verify the column header matches the exact semantic role before extracting.\n- For inclusive time-series ranges, enumerate all expected observations and search for any missing ones before computing from a partial set.\n- Do not round intermediate operands or conversion factors — round only the final result to the requested precision.\n- When multiple nearby sections have similar labels, use only the section whose title matches the requested measure exactly (e.g., fiscal-year vs calendar-year sections).\n- Do not append '%', unit words, or currency symbols unless the question explicitly asks for a percentage, named unit, or currency-formatted output."
}
```
