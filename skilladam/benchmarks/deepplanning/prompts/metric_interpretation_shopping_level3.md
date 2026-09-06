### Evaluation Metrics

The evaluator compares the final cart state (product IDs and applied coupons)
against a ground-truth set. Text output is not evaluated — only tool-call side
effects matter.

The evaluation focuses on product selection accuracy and coupon optimization.
The agent must select the correct products AND apply the optimal coupon
combination to minimize total cost. The ground truth includes both a target
product set and a target coupon set.

**Metrics in the trajectory label:**

- `outcome`: success if all target products AND all target coupons are matched
  exactly; failure otherwise.
- `matched`: X/Y — X items matched the ground truth, out of Y expected.
  The denominator Y counts products + coupons combined (e.g., 4 products +
  2 coupons = expected 6).
- `extra`: number of products in the cart that are not in the ground truth.
- `coupon_score`: fraction of ground-truth coupons correctly applied.
  1.0 = all coupons matched; 0.0 = none matched; 0.5 = partial match.

**How to read:**
- `matched=6/6 | extra=0 | coupon_score=1.0` → perfect: all products and
  all coupons correct.
- `matched=4/6 | extra=1 | coupon_score=0.5` → missed 2 items (could be
  products or coupons), added 1 wrong product, and only half the expected
  coupons were applied. The agent likely selected some wrong products and
  used a suboptimal coupon strategy.
- `matched=4/4 | extra=0 | coupon_score=0.0` → all products correct but
  no coupons applied. The agent skipped coupon optimization entirely.
