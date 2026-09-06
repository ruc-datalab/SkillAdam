### Trajectory Outcome Labels

Each trajectory carries one of these labels:

```
outcome=success | EM=1    — model selected the correct choice
outcome=failure | EM=0    — model selected a wrong choice
```

The format of each trajectory summary:
```
**Q**: <question text>
**Choices**: A. ... | B. ... | C. ... | D. ...
**Gold**: <correct label>
**Predicted**: <model's label>
**EM=<0|1>**
```
