---
name: assembling-multi-item-shopping-carts
description: Assemble a shopping cart containing multiple products that each satisfy distinct requirement sets while minimizing total cost. Use when the user requests several different items with specific constraints on attributes, ratings, delivery, or sales metrics.
when_to_use:
  - The user requests multiple distinct products (3+ items) in a single query
  - Each product has its own set of requirements involving brand, color, size, ratings, reviews, sales, stock, or delivery constraints
  - The task requires finding the cheapest option for each product type that meets all specified criteria
---

# Assembling Multi-Item Shopping Carts

## Workflow

1. **Parse requirements systematically**: Break down the user's request into separate product requirements. For each product, identify all mandatory constraints (brand, color, size, rating thresholds, review counts, sales volumes, stock levels, delivery windows). Check the user profile for missing details like size or location.

2. **Search and filter progressively**: For each product requirement, start with a broad search, then apply filters to narrow candidates. Use attribute filters before retrieving full details to reduce unnecessary data fetching. When a search returns no results, try alternative query formulations before concluding the product is unavailable.

3. **Validate every constraint**: Retrieve full product details for all remaining candidates and verify each constraint explicitly. Do not assume a product meets a threshold without checking the actual value. When multiple candidates satisfy all requirements, select the lowest-priced option.

4. **Verify delivery feasibility**: Check delivery times for all finalists before adding to cart. Ensure each product meets its specific delivery requirement based on the user's location.

5. **Finalize cart and confirm**: Add selected products sequentially, then retrieve the final cart state to verify all items are present and calculate the total cost.

## Error Avoidance

- Do not add a product that violates any stated constraint, even if it is the only candidate or the cheapest option.
- Do not select a higher-priced product when a cheaper alternative meets all requirements.
- Do not skip delivery time verification — products that meet all other criteria may still fail delivery constraints.
- When a product requirement cannot be satisfied, do not substitute it with an item that serves a different purpose or violates the original constraints.
- Do not assume that a product meeting one requirement automatically satisfies another distinct requirement unless explicitly verified.
- When the user specifies a product by name or keyword (e.g., 'Performance Tights', 'HOVR Phantom'), only products whose names contain that exact keyword are eligible candidates. Price optimization applies only among qualifying name-matched products — a cheaper product with a different name does not qualify.
- When an individual product requirement explicitly specifies a target season (e.g., 'a winter jacket', 'summer shoes'), prefer products whose season attribute matches that season over 'All Seasons' alternatives. Do not apply overall query context (e.g., 'shopping for a winter trip') as a season constraint on requirements that do not explicitly mention a season — for those requirements, select the cheapest product meeting all stated constraints regardless of its season attribute.