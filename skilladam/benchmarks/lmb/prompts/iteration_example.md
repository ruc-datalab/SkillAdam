The content below is illustrative only.

```json
{
  "reasoning": "Two failures promoted existential conclusions to universal ones, while a success explicitly rewrote the quantifiers before comparing options. Strengthen the existing parsing step instead of adding a broad new checklist.",
  "patch": "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -3,3 +3,4 @@\n ## Workflow\n \n-- Identify the theorem used by the question.\n+- Rewrite every quantifier and its scope in plain language before applying the theorem; separately track existence, uniqueness, and universal claims.\n"
}
```
