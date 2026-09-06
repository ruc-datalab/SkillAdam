# SearchQA Failure Types

The iteration agent and momentum agent both classify every observed
failure into exactly one of the following types. Use these names
character-for-character; do not invent new types here.

- **rule_missing**: The skill lacks any rule that would cover this class of question, and adding one is the appropriate fix.
- **rule_wrong**: An existing skill rule actively misleads the agent toward the wrong answer; the right fix is to revise or remove that rule, not to add more.
- **rule_ignored**: The skill already contains a rule that would have produced the correct answer, but the rollout agent did not follow it. Adding another similar rule typically does not help — strengthen or concretize the existing entry instead.
- **answer_format**: The agent identified the correct underlying entity / fact but produced the wrong surface form. Failures live at the level of casing, punctuation, singular vs plural, common name vs full legal name, diacritics, conventional abbreviations, exact-match tokenization, etc.
- **other**: None of the above. Use this when the failure stems from ambiguous evidence, irreducible model variance, scoring noise, or anything that is not a skill-fixable mistake.
