Input (2 trajectories from a document-editing domain):

```
[1] outcome=success

**Query**: Convert a batch of 5 scanned PDF invoices into structured JSON records.

**Tool Call Chain**:
1. `list_files` on input directory — found 5 PDF files.
2. `ocr_extract` on each PDF — 3 clean, 2 with artifacts.
3. `validate_json_schema` — 3 passed, 2 failed.
4. Re-extracted the 2 failed files with enhanced mode — both passed on retry.
5. `write_output` saved 5 JSON files.

---

[2] outcome=failure

**Query**: Extract tables from a 20-page financial report PDF and export as CSV.

**Tool Call Chain**:
1. `ocr_extract` — table structure lost in OCR.
2. `parse_table` — failed to identify table boundaries.
3. Retried with page-level image extraction — 15 pages recovered, 5 remained garbled.
4. Saved partial CSV. 5 pages missing.
```

Output:

```json
{
  "metadata_json": {
    "name": "extracting-structured-data-from-pdfs",
    "description": "Extract structured data from PDF documents into machine-readable formats. Use when the user needs to convert PDF content into JSON, CSV, or other structured outputs.",
    "when_to_use": [
      "The user needs to extract text, tables, or structured data from PDF files",
      "The task involves converting scanned or image-based documents into structured formats"
    ]
  },
  "skill_body_md": "# Extracting Structured Data from PDFs\n\n## Workflow\n\n1. **Assess inputs**: Determine how many files need processing and whether they are text-based or scanned.\n2. **Extract content**: Process each file. For scanned documents, expect imperfect results on the first pass.\n3. **Validate outputs**: Check every extracted record against the expected structure before finalizing.\n4. **Retry failures**: When extraction or validation fails, re-process with a different strategy rather than accepting partial results.\n5. **Assemble final output**: Write validated records and report any unresolvable gaps.\n\n## Error Avoidance\n\n- Do not accept partial extraction results without attempting an alternative extraction strategy.\n- Do not skip validation between extraction and output — silent errors compound downstream.\n- When table structure is lost during extraction, switch to page-level processing rather than retrying the same method."
}
```
