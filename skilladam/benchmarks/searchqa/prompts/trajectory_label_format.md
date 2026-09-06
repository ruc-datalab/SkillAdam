Each trajectory label has the format:

```
outcome=success|failure | EM={0|1} | F1={score}
```

- `outcome=success` means EM=1 (exact match with a gold answer).
- `outcome=failure` means EM=0 (predicted answer does not match any gold).
- `F1` is the token-level overlap — useful for distinguishing near-misses
  (high F1) from completely wrong answers (F1=0).
