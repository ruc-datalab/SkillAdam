### Evaluation Metrics

The evaluator compares the final cart state (product IDs) against a ground-truth
product set. Text output is not evaluated — only tool-call side effects matter.

The evaluation focuses on product selection accuracy.

**Metrics in the trajectory label:**

- `outcome`: success if all target products are matched exactly; failure otherwise.
- `matched`: X/Y — X products in the cart matched the ground truth, out of Y expected.
- `extra`: number of products in the cart that are not in the ground truth.
  These are wrong or unnecessary selections.

**How to read:**
- `matched=4/4 | extra=0` → perfect product selection.
- `matched=3/4 | extra=1` → missed 1 target product and added 1 wrong product.
  The agent may have confused a similar product for the correct one, or applied
  an incorrect selection criterion.
- `matched=4/4 | extra=2` → all targets found, but 2 extra products were added
  unnecessarily.
