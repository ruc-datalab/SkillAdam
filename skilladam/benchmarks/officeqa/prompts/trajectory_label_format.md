### Trajectory Outcome Labels

Each trajectory carries one of these labels:

```
outcome=success | EM=1, F1=1.00    — exact match after normalization
outcome=partial | EM=0, F1=0.XX    — partial token overlap (some correct tokens)
outcome=failure | EM=0, F1=0.00    — no token overlap with gold answer
```

### Trajectory Summary Format (after LLM compression)

Each compressed trajectory includes:
```
**Q**: <question text>
**Gold**: <normalized gold answer>
**Predicted**: <normalized predicted answer>
**EM=<0|1>, F1=<score>**
**Tool Calls**: <count> calls across <turns> turns
**Key Actions**: <compressed summary of retrieval steps, files accessed, evidence found>
```
