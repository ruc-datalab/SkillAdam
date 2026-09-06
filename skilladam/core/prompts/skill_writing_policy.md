# Skill Writing Policy

Follow this as a strict writing policy to ensure alignment with the project's runtime contract.

## 1. Project Skill Structure

In this project, a skill has two artifacts:

1. `metadata.json` (trigger metadata)
2. `SKILL.md` (body only, no frontmatter)

Your output must provide values that can populate these artifacts.

## 2. Metadata Contract

`metadata.json` fields:

1. `name`: retrieval label (max 64 characters, lowercase letters/numbers/hyphens only)
2. `description`: primary trigger summary (max 1024 characters, must be non-empty)
3. `when_to_use`: explicit trigger conditions

### 2.1 `name`

1. Use one short human-readable phrase in **gerund form** (verb + -ing), as this clearly describes the activity or capability the skill provides.
2. Name one coherent capability boundary.
3. Only use lowercase letters, numbers, and hyphens.
4. Do not use vendor/model brand names, XML tags, IDs, shard/run tokens, case-specific entities, or evaluator-internal terminology.
5. **Domain vocabulary is permitted and encouraged** when the skill serves a specific task domain. The dispatcher matches skills by surface-level signals; hiding the domain behind abstract structural terms hurts retrieval. The ban in rule 4 targets *case-level* identifiers (specific product IDs, case numbers, run tokens), not *domain-level* terms (shopping, travel, coupon, itinerary).

Good examples (gerund form, domain-concrete):
- `processing-pdfs`
- `analyzing-spreadsheets`
- `managing-databases`
- `testing-code`
- `writing-documentation`
- `shopping-with-coupon-optimization`
- `travel-itinerary-planning`

Acceptable alternatives:
- Noun phrases: `pdf-processing`, `spreadsheet-analysis`
- Action-oriented: `process-pdfs`, `analyze-spreadsheets`

Bad examples:
- Vague names: `helper`, `utils`, `tools`
- Overly generic: `documents`, `data`, `files`
- Brand/model names: `gpt-helper`, `llm-tools`
- Over-abstracted away from the domain: `optimizing-multi-constraint-purchases` (for a shopping skill), `planning-constrained-sequences` (for a travel skill). Prefer names that echo the domain the skill actually serves.

### 2.2 `description`

1. This is the strongest trigger field. The runtime uses it to select the correct skill from potentially 100+ available skills.
2. It must include both:
   - what the skill does,
   - when it should be used.
3. **Always write in third person.** The description is injected into system prompts; inconsistent perspective can cause discovery issues.
4. Keep it concise (1-2 sentences). Include specific key terms and concrete triggers/contexts.
5. Prefer explicit trigger wording like `Use when ...`.

Good examples:

```
Extract text and tables from PDF files, fill forms, merge documents. Use when processing PDF files or when the user mentions PDFs, forms, or document extraction.
```

```
Analyze Excel spreadsheets, create pivot tables, generate charts. Use when analyzing Excel files, spreadsheets, tabular data, or .xlsx files.
```

```
Generate descriptive commit messages by analyzing git diffs. Use when the user asks for help writing commit messages or reviewing staged changes.
```

```
Use when a travel request has hard date, budget, and opening-hour constraints that must all remain valid after itinerary changes.
```

Bad examples:

- `Helps with planning tasks.` (vague, no trigger context)
- `Handles documents.` (no specificity about what or when)
- `Does various things with files.` (completely uninformative)
- `I can help you process Excel files.` (first/second person — must use third person)
- `You can use it to process Excel files.` (second person — must use third person)

### 2.3 `when_to_use`

`when_to_use` is read by a **dispatcher** that only sees the user's incoming request.
The dispatcher's sole question is: "Should I load this skill for this request?"

Litmus test for every item: **Can this condition be checked by reading ONLY the user's request, before the skill runs?**

**Abstraction level**: Write conditions at the **operational pattern** level — specific enough to be falsifiable from the user's request, general enough to cover the task category the skill actually serves.

- If the skill is **domain-specific** (e.g., written for shopping, travel, code review), domain vocabulary in `when_to_use` is encouraged. The dispatcher needs surface-level signals to select this skill over hundreds of others; stripping the domain vocabulary makes triggering unreliable.
- If the skill is **genuinely cross-domain** (e.g., a generic PDF toolkit that works for any PDF), lift conditions to a structural level so similar tasks in other surface domains can still trigger.
- Balance litmus: "Could another domain legitimately trigger this condition, and do I want it to?" If the answer is "no, this skill only serves domain X," keep the domain word. If the answer is "yes, I want this skill to serve multiple domains," generalize.

Each condition must be **specific enough to be falsifiable** — a reader should be able to point to part of the user's request and say "yes, this condition is met" or "no, it is not." Generalization does not mean vagueness.

1. Provide concrete trigger conditions as a string array.
2. Each condition must be something visible in the user's request: mentioned entities, stated constraints (e.g., budget, dates, file types), or task-type signals.
3. Each item should stand alone and be testable by a reader.
4. Each item must describe an **observable input condition from the user's side**, not how the skill executes, what tools it uses, what rules it follows, or what format it outputs.
5. Avoid overlap-only restatements of `description`.
6. Do not include evaluator fields, case IDs, or hidden target details.

Good example — cross-domain skill, structural level is appropriate:

```json
[
  "The user needs to extract structured content from a binary container format (e.g., PDF, DOCX, XLSX)",
  "The task requires parsing or transforming a file format that needs specialized libraries rather than plain-text editing",
  "User mentions filling forms or merging documents within a container format",
  "A batch of files requires consistent structural modifications while preserving existing metadata"
]
```

These describe the structural pattern (binary container manipulation) while remaining specific enough to verify against any real request. This level is appropriate **because the skill genuinely serves multiple container formats**.

Good example — domain-specific skill, domain vocabulary is appropriate:

```json
[
  "The user asks to select or purchase products subject to budget, quantity, or attribute constraints",
  "The user mentions coupons, discounts, promotions, or cost optimization in a shopping context",
  "The request references a shopping cart, product catalog, or e-commerce workflow",
  "The user provides product filtering criteria (price, ratings, stock, shipping time) that must jointly hold"
]
```

Domain vocabulary ("products", "coupons", "shopping cart") is retained because the skill is designed specifically for shopping workflows. Stripping these terms to "structural" phrasing (e.g., "multi-constraint selection over a catalog") would make the dispatcher unable to reliably distinguish this skill from unrelated optimization skills.

Bad example — over-abstracted (domain skill written as if cross-domain):

```json
[
  "The user needs multi-constraint selection with cost optimization",
  "The task involves filtering candidates under attribute constraints and a budget",
  "The request requires optimizing cumulative cost under discount rules"
]
```

These read as generic optimization tasks. A shopping-specific skill should keep shopping vocabulary so the dispatcher can select it over, e.g., a portfolio-allocation skill or a resource-scheduling skill.

Bad example — too vague (not falsifiable):

```json
[
  "User needs help with complex data processing",
  "The task involves multiple steps",
  "Some kind of optimization is required"
]
```

These match almost any request and provide no discriminating power.

Bad example — single-format-locked (skill genuinely serves more but triggers are narrower than it should be):

```json
[
  "User asks to extract text from a PDF file",
  "User mentions filling PDF forms or merging PDF documents",
  "User references .pdf files"
]
```

Only a problem **when the skill actually serves more formats** (DOCX, XLSX, PPTX). If the skill is truly PDF-only, these conditions are appropriate; if it covers a broader container class, broaden the triggers accordingly.

Bad example — execution constraints and output requirements (belong in body, not in `when_to_use`):

```json
[
  "All data must come exclusively from tool outputs",
  "The schedule must obey strict continuity and geospatial rules",
  "A formatted day-by-day itinerary with budget summary is required"
]
```

These describe how the skill works or what it produces — the dispatcher cannot check any of them from the user's request. Corresponding user-side structural rewrites:

- `"All data must come exclusively from tool outputs"` -> `"The user's request involves real-world entities whose details must be looked up rather than generated from general knowledge"`
- `"The schedule must obey strict continuity and geospatial rules"` -> `"The task requires assembling a sequence of activities with hard temporal and spatial dependencies"`
- `"A formatted day-by-day itinerary with budget summary is required"` -> `"The user needs a structured multi-step plan with resource tracking across steps"`

## 3. SKILL.md Body Policy

`SKILL.md` is loaded after trigger; it should focus on execution.

1. Write clear, actionable workflow steps.
2. Prefer a default path plus explicit fallback conditions. Do not present multiple methods unless necessary.
3. Include key checks and failure prevention steps.
4. Keep wording operational and concise. Keep SKILL.md body under 500 lines.
5. Avoid long conceptual lectures and avoid benchmark narrative. Assume the LLM is already intelligent — only add context it would not already know.
6. Do not add a separate "When to use this skill" section in body; trigger logic belongs in metadata.
7. When providing format templates or examples in the body, use bracket placeholders (e.g., `[place name]`, `[price]`, `[flight number]`) instead of concrete entity values. Concrete values in templates risk being copied verbatim into real outputs.
8. When the input material includes an `available_tools` section, the workflow in the body **must** describe how to accomplish the task through tool calls, not by directly generating the final output. The skill should specify which tools to call, in what order, and what to check after each call. Do not instruct the agent to "output" or "list" data that can only be obtained by calling a tool.

### 3.1 Conditional Workflow Pattern

Real user requests within a single domain often emphasize different aspects (for
shopping: product-spec matching vs. price/coupon optimization; for travel:
itinerary construction vs. budget tightening; for code work: new-feature
scaffolding vs. refactor of existing code). A rigid linear workflow that assumes
one dominant aspect will either skip work the user cares about or force work the
user did not ask for.

When the input material suggests multiple legitimate request shapes within the
skill's domain, the body **should** use an explicit branching pattern:

1. **Phase 0 — classify the request.** At the very start of the workflow,
   enumerate the aspects the skill is prepared to handle and define observable
   detection criteria for each. Criteria must be checkable from the user's
   request text (constraints mentioned, entities referenced, quantities stated).
2. **Composable execution paths.** Define a named path per aspect. Paths must be
   composable — a request that invokes multiple aspects should execute the
   corresponding paths in sequence, not pick one and discard the rest.
3. **Graceful skipping.** When an aspect is absent from the request, the
   corresponding path is skipped with a single-line note, not an error and not a
   refusal. The skill's scope is the union of its aspects; no single aspect is a
   prerequisite for the others.

Example skeleton:

```markdown
### Phase 0: Classify Request

Determine which aspects the user's request emphasizes:
- **Aspect A — product-spec matching**: user names product attributes, quantity, ratings, stock, or shipping requirements.
- **Aspect B — cost optimization**: user mentions budget, discounts, coupons, or lowest-price.

Routing:
- If A and B: run Phase 1A, then Phase 1B against A's filtered candidates.
- If only A: run Phase 1A, skip Phase 1B (do not invent a budget).
- If only B: run Phase 1B with the minimal product constraints implied by the request.
```

**Anti-pattern — STOP gates.** Do not instruct the agent to refuse the task
because it only partially matches the skill's scope. If the user's request
covers some aspects of the domain and not others, execute the matching aspects
and skip the non-matching aspects. The skill body MUST NOT contain lines of the
form "STOP and respond: ...", "refuse this task", or "this skill does not
apply" as a response to partial aspect coverage. Lack of a coupon mention, lack
of a budget, or lack of a stated time window are not reasons to abort — they
are routing signals.

**Anti-pattern — single-default-then-fallback masquerading as branching.**
"Always do X; if X fails, do Y" is not a conditional workflow. Real branching
routes on *input* signals before execution, not on *failure* after execution.
Use the default-plus-fallback pattern only for well-defined degradation (e.g.,
tool unavailability), not for choosing which aspect of the user's request to
serve.

Bad example — too many choices with no default:
```markdown
You can use pypdf, or pdfplumber, or PyMuPDF, or pdf2image, or ...
```

Good example — provide a default with an escape hatch:
````markdown
Use pdfplumber for text extraction:

```python
import pdfplumber
with pdfplumber.open("file.pdf") as pdf:
    text = pdf.pages[0].extract_text()
```

For scanned PDFs requiring OCR, use pdf2image and pytesseract instead.
````

## 4. Generalization and Portability

1. Capture recurring domain patterns, not one-off fixes.
2. Do not encode fixed entities, fixed answer values, or case artifacts.
3. Keep terminology consistent across metadata and body. Pick one term and use it throughout.
4. Keep the skill composable; do not absorb unrelated capabilities.

Good — consistent:
- Always "API endpoint" (not mixing "URL", "API route", "path")
- Always "field" (not mixing "box", "element", "control")
- Always "extract" (not mixing "pull", "get", "retrieve")

## 5. Token and Organization Guidance

1. Keep body compact and easy to scan.
2. Keep body focused on core reusable workflow. Each skill is self-contained in a single `SKILL.md`; do not reference external files.

## 6. Final Quality Checklist

A draft is acceptable only if all pass:

1. Metadata alone can help runtime decide loading.
2. `name` uses gerund form, lowercase-hyphens only, max 64 characters, names one coherent capability.
3. `description` clearly states both function and trigger in third person, max 1024 characters.
4. `when_to_use` contains only conditions a dispatcher can check from the user's request alone (no execution rules, tool constraints, or output format requirements).
5. Body contains a clear workflow with meaningful checks, under 500 lines.
6. Terminology is consistent across metadata and body.
7. No evaluator-internal leakage (scoring thresholds, check names, judgment logic), provenance residue (case IDs, run tokens), or evaluator bookkeeping. Tool names, parameters, and response schema from the task environment are permitted — they are available to the agent at runtime.
