### Evaluation Metrics

The evaluator scores the structured itinerary content — not text formatting.
The agent's natural-language plan is parsed into structured JSON, then scored
against ground-truth constraints and a local database of real transportation,
hotel, attraction, and restaurant records.

**Metrics in the trajectory label:**

- `outcome`: success if both CS=1.0 and PS=1.0; failure otherwise.

- `CS` (Commonsense Score): Weighted average across 8 binary dimensions
  (each 0.125 weight). Measures whether the itinerary is logically sound:
  - **Route Consistency**: valid trip duration, closed-loop route, seamless
    intercity transfers.
  - **Sandbox Compliance**: all hotels, attractions, restaurants, and
    transportation in the plan exist in the database.
  - **Itinerary Structure**: accommodation is traceable, each day ends at
    accommodation, adequate meals and attractions per day.
  - **Time Feasibility**: no time overlaps between activities, transfer
    times between locations are realistic.
  - **Business Hours**: attractions visited during opening hours, meals
    during service hours, no visits on closure days.
  - **Duration Rationality**: visit durations at attractions and meal
    durations are within reasonable ranges.
  - **Cost Calculation Accuracy**: total budget in the plan matches the
    sum of individual costs within 10% tolerance.
  - **Activity Diversity**: no duplicate restaurants or attractions across
    the entire trip.

- `PS` (Personalized Score): Binary — all user-specified hard constraints
  must pass for PS=1.0. Constraint types include:
  - Transportation: earliest/cheapest/fastest train or flight, seat class,
    departure/arrival time range.
  - Accommodation: star rating, specific amenities, brand, price range,
    newest decoration.
  - Attractions: must-visit named attractions, all attractions of a type,
    highest-rated of a type, all free attractions.
  - Dining: specific named restaurant, cheapest/highest-rated near an
    attraction, specific cuisine type, specific feature tag (waiting area,
    online queue).
  - Budget: total cost within stated limit.

- `failed_dimensions` (failure trajectories only): lists the CS dimensions
  that scored 0 in this run.

**How to read:**
- `CS=1.0 | PS=1.0` → perfect itinerary.
- `CS=0.625 | PS=1.0 | failed: Time Feasibility, Cost Calculation, Itinerary
  Structure` → user preferences satisfied, but 3 commonsense dimensions
  failed. The plan has scheduling or cost errors.
- `CS=0.875 | PS=0.0` → itinerary is mostly sound but user-specific
  constraints were not met (e.g., wrong hotel star, missed a required
  attraction, exceeded budget).
