### Trajectory Outcome Labels

Each trajectory carries one of these labels:

```
outcome=success | hard=1    — agent completed the task within step budget
outcome=failure | hard=0    — agent did not complete the task
```

### Trajectory Summary Format (after LLM compression)

Each compressed trajectory includes:
```
**Task type**: <task category>
**Task**: <goal description>
**Outcome**: <success/failure> (<N> steps)
**Fail reason**: <reason, if failed>
**Key actions**: <compressed summary of navigation, interactions, and outcome>
```
