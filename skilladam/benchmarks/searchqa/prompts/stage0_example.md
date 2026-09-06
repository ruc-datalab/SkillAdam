Input (2 trajectories from an abbreviation lookup domain — single-turn QA):

```
[1] outcome=success | EM=1 | F1=1.0

**Question**: What does "HTTP" stand for?
**Gold answer**: ["Hypertext Transfer Protocol"]
**Predicted**: "Hypertext Transfer Protocol"

---

[2] outcome=failure | EM=0 | F1=0.7

**Question**: What does "DNS" stand for?
**Gold answer**: ["Domain Name System"]
**Predicted**: "Domain Name System (DNS) — a hierarchical naming system"
```

Output:

```json
{
  "metadata_json": {
    "name": "abbreviation-and-initialism-lookup",
    "description": "Look up the canonical expansion of common abbreviations and initialisms. Use when the user asks 'What does X stand for?' or 'What is the full form of X?'.",
    "when_to_use": [
      "The user asks for the full form of an abbreviation or initialism",
      "The task requires extracting the short canonical name from a longer descriptive phrase"
    ]
  },
  "skill_body_md": "# Abbreviation and Initialism Lookup\n\n## Workflow\n\n1. **Identify the canonical form**: locate the standard expansion of the abbreviation as commonly cited in references. Prefer the form most widely used in technical documentation.\n2. **Output only the canonical form**: return only the canonical expansion. Do not include descriptive elaboration, the original abbreviation in parentheses, or qualifiers about its function or scope.\n\n## Error Avoidance\n\n- Do not append parenthetical descriptions or definitions to the canonical form.\n  - 'Hypertext Transfer Protocol' not 'Hypertext Transfer Protocol (HTTP) — a stateless application-layer protocol'\n  - 'Domain Name System' not 'Domain Name System (DNS) — a hierarchical naming system'\n- Do not include the original abbreviation in parentheses after the expansion.\n  - 'Light Emitting Diode' not 'Light Emitting Diode (LED)'\n- Do not concatenate alternative spellings or variant capitalizations.\n  - 'World Wide Web' not 'World Wide Web / WWW / web'"
}
```

Note how the example skill body attaches concrete `(correct, incorrect)` example
pairs as sub-bullets directly under each Error Avoidance rule. When the failure
mode (over-expansion, parenthetical descriptions) recurs across multiple
trajectories, paired examples bind decoder behavior more strongly than abstract
restatement of the rule. Apply the same pattern when generating the initial
skill: if the failure pool reveals a clear recurring error type, attach 1–2
short concrete pairs sourced from the failure trajectories themselves.
