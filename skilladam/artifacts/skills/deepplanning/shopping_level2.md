---
name: multi-item-budget-constrained-shopping
description: Assembles a shopping cart containing multiple items, each with distinct attribute requirements, while respecting a specified budget range. Use when the user requests several products with individual constraints (brand, color, size, ratings, reviews, delivery time, stock) and provides a total spending limit.
when_to_use:
  - The user requests multiple distinct products (3+ items) in a single query
  - Each product has specific attribute requirements such as brand, color, size, rating thresholds, review counts, delivery time, or stock levels
  - The user specifies a total budget range or maximum spending limit
  - The task requires finding the optimal combination of products that satisfies all constraints while minimizing total cost
---

# Multi-Item Budget-Constrained Shopping

## Workflow

1. **Parse requirements systematically**: Extract each item's constraints separately, noting which attributes are mandatory versus flexible. Identify the budget boundaries as hard limits.

2. **Retrieve candidate pools**: For each required item, gather all products matching the core identifying attributes (brand, category, color, size). Cast a wide initial net before applying numeric filters.

3. **Apply constraint filters progressively**: Narrow each candidate pool by checking rating thresholds, review distributions, sales volumes, stock levels, and delivery times. Verify delivery constraints by calculating transport time to the user's location when speed requirements exist.

4. **Enumerate valid combinations**: Once you have qualifying candidates for each item, identify all possible product combinations where every item's constraints are satisfied. Do not prematurely select a single option per item.

5. **Optimize within budget**: Calculate the total price for each valid combination. Eliminate any combination exceeding the maximum budget. From the remaining options, select the combination with the lowest total cost. If no combination fits the budget, report the shortfall and present the cheapest alternative.

6. **Verify final state**: Add all selected products to the cart in sequence, then confirm the cart contents and total price match your calculations before concluding.

## Error Avoidance

- Do not select the cheapest qualifying product for each item independently without checking whether the resulting combination fits the budget — individual optimization does not guarantee global budget compliance.
- Do not ignore the budget minimum when one exists — selecting a combination below the lower bound violates the constraint even if it seems cost-effective.
- Do not skip delivery time verification when transport speed is specified — assume delivery varies by destination and must be calculated explicitly.
- Do not treat missing search results for one item as a dead end — broaden the query or search adjacent categories to find candidates that meet the functional requirement.