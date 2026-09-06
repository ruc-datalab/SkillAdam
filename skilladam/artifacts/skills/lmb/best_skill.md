## Workflow

1. **Isolate the precise theorem conclusion**: Identify every clause of the claim — all hypotheses, quantifiers, boundary cases, and every conclusion clause marked by words like "moreover" or "in fact." List every distinct feature the theorem actually proves; you will need this complete list in steps 3 and 6.

2. **Compare each option against the theorem one clause at a time**: The correct choice is the strongest claim the theorem fully supports; nearby distractors either weaken it, overstate it, or shift a detail. Track exact quantifiers: "there exists" vs "for every," "if and only if" vs "only if," endpoint inclusion vs exclusion, open interval vs closed interval.

3. **Detect and evaluate the meta-option**: Check whether one option asserts "the theorem proves a strictly stronger result than any of A–D" or equivalent. If such a meta-option exists, apply this test: for each concrete option, check whether it captures ALL features from step 1. If every concrete option is missing at least one feature, the meta-option is correct — select it immediately.
   - The meta-option is correct whenever any single feature (an extra quantifier, sharper bound, additional clause, or tighter regularity) is present in the theorem but absent from every concrete option.
   - The meta-option text is deliberately short (typically one sentence). Its brevity is NOT a signal of weakness — it is expected. Do not prefer a longer concrete option merely because it "looks more mathematical."
   - A concrete option that appears detailed or uses sophisticated notation is NOT automatically better than the meta-option; specificity is not completeness.
   - The meta-option does NOT add claims beyond the theorem; it merely asserts the theorem's actual conclusion exceeds what the concrete options individually state.

4. **Prefer precision over simplicity — but reject overstatement**: An option that gives an explicit constant, exact formula, or quantitative rate is stronger than one asserting mere existence or finiteness — prefer the quantitative form when the theorem provides it. However, an option that adds a converse, broadens the parameter range, or upgrades regularity (e.g. $C^2$ when the theorem only proves $C^{1,\alpha}$) is overstrong — reject it regardless of its apparent completeness. When two options share the same opening clause, focus on the final diverging clause and check whether the theorem supports the broader or narrower conclusion.

5. **Verify hypotheses and scope**: Check domain, regularity class, and parameter restrictions. Pay close attention to extremal conditions, equality versus strict inequality, and whether the result applies to the full family or a restricted subfamily.

6. **Decompose and verify your candidate before answering**: Before committing to any option, split it into its individual claims (each conjunction, "moreover" clause, convergence rate, parameter range, quantifier). For each claim in the option, confirm it appears explicitly in the theorem's conclusion or follows immediately from it. If EVEN ONE claim in the option is not justified by the theorem, that option is overstrong — switch to a weaker alternative or to the meta-option. This step catches the "subsumption trap": an option that includes the correct claim plus an extra unproven assertion.

## Error Avoidance

- **Subsumption trap**: When option X starts with the same text as option Y and then appends "moreover," "and furthermore," or an additional formula, the appended claim is likely NOT proven by the theorem. Do not select X merely because it "includes" Y; verify the appended part independently. If you cannot confirm the extra claim, prefer the shorter option Y (or the meta-option if applicable).

- Do not confuse the meta-option with an overstatement. The meta-option identifies a gap between the theorem and the concrete options; it does not invent new claims. When uncertain between a concrete option and the meta-option, default to the meta-option.

- Quantifier and interval precision: "for every" is strictly stronger than "there exists"; "if and only if" is strictly stronger than "only if"; a closed interval $[0,T]$ is stronger than an open interval $(0,T)$; "for every $\gamma\in(0,1)$, there exists $\Sigma$" (universal) is stronger than "there exists $\gamma\in(0,1)$ and $\Sigma$" (existential). Pick the EXACT quantifier level and interval type the theorem actually proves — not the stronger form you might expect.

- Do not confuse regularity claims: $C^{1,\alpha}$ on a punctured domain is weaker than $C^2$ on the full domain. Match the exact regularity and domain the theorem establishes.

- Do not select an option merely because it appears more detailed, longer, or uses more notation. Added length signals added claims — and added claims are the most common source of error. Between two plausible options, prefer the one that does NOT introduce an extra clause, condition, or implication beyond the theorem's stated conclusion.
