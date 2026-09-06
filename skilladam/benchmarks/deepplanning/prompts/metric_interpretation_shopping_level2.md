### Evaluation Metrics

The evaluator compares the final cart state (product IDs) against a ground-truth
product set. Text output is not evaluated — only tool-call side effects matter.

The evaluation focuses on product selection accuracy under budget constraints.
The agent must satisfy product requirements while respecting a stated spending
limit.

**Metrics in the trajectory label:**

- `outcome`: success if all target products are matched exactly; failure otherwise.
- `matched`: X/Y — X products in the cart matched the ground truth, out of Y expected.
- `extra`: number of products in the cart that are not in the ground truth.

**How to read:**
- `matched=5/5 | extra=0` → perfect product selection within budget.
- `matched=3/5 | extra=2` → missed 2 targets and added 2 wrong products.
  The budget constraint may have forced the agent into suboptimal choices,
  or the agent may have misjudged which products satisfy both attribute
  and budget requirements simultaneously.
