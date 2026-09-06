---
name: optimizing-multi-item-shopping-carts
description: Assembles shopping carts with multiple products meeting specific constraints while minimizing total cost through strategic coupon application. Use when the user requests several items with detailed requirements (ratings, reviews, stock, delivery, size) and has a budget or available coupons.
when_to_use:
  - The user requests multiple products (3+ items) with specific attribute constraints
  - Requirements include quantitative thresholds for ratings, reviews, sales, stock, or delivery time
  - The user has available coupons or a budget constraint that requires cost optimization
---

# Optimizing Multi-Item Shopping Carts

## Workflow

1. **Parse requirements systematically**: Break down each item's constraints into verifiable criteria before searching. When requirements reference user attributes (size, location), retrieve the user profile first to inform all subsequent searches.

2. **Search and filter strategically**: Use broad searches initially, then apply filters to narrow candidates. When searches return zero results, verify whether the query terms are too specific or whether alternative search strategies (filtering by attributes rather than keywords) would yield candidates.
   For requirements involving specific measurable attributes (exact review counts, sales thresholds, stock levels), prefer attribute filters like `filter_by_range` over keyword searches—keyword searches are unreliable for locating products by numeric criteria.

3. **Validate every constraint**: Retrieve full details for **all** candidates returned by any filter or search before selecting — do not stop after finding the first qualifying product. Verify each requirement explicitly against the full candidate set. Pay special attention to multi-dimensional constraints (e.g., review distributions requiring both high positive counts AND low negative counts).

4. **Check delivery feasibility**: Calculate transport times for location-dependent requirements before finalizing selections. Delivery constraints are hard requirements—products failing these thresholds cannot substitute for missing alternatives.

5. **Optimize total cost**: After identifying all qualifying products, calculate the final price with all applicable coupons. Verify coupon eligibility by checking thresholds against the cart total. Apply the maximum discount possible within stacking rules.
   Test coupon combinations exhaustively by trying all viable subsets. **Explicitly record the lowest final price and its corresponding coupon set as you test.** When all combinations have been tried, **restore that best-recorded combination** before finalizing — do not leave the cart in the state of the last combination tested, which may not be optimal.

6. **Verify completeness**: Confirm the cart contains exactly the requested number of items and that all requirements are satisfied before finalizing.

## Error Avoidance

- Do not add products that fail hard constraints (delivery time, size, stock) even when no alternatives exist—partial fulfillment without disclosure misrepresents the outcome.
- Do not skip coupon application after assembling the cart—cost optimization is a primary objective, not an optional step.
- When searches return no results, do not proceed with fewer items than requested without attempting alternative search strategies or filters.
- Do not assume review distribution constraints are met without inspecting the full breakdown—products with high overall ratings may still fail specific star-count thresholds.