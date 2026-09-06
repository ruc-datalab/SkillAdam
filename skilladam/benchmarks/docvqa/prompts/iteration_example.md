The content below is illustrative only.

```json
{
  "reasoning": "Several partial outcomes found the right table cell but added an unrequested unit, so tighten the existing formatting rule.",
  "patch": "--- a/SKILL.md\n+++ b/SKILL.md\n@@ -8,3 +8,3 @@\n ## Error Avoidance\n \n-- Keep answers concise.\n+- Return the smallest supported span; append a unit only when the question requests it or the printed value forms one indivisible phrase.\n"
}
```
