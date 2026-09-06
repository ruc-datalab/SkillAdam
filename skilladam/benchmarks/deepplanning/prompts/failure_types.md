# DeepPlanning failure types

- **constraint_violation**: One or more explicit task constraints are violated.
- **tool_misuse**: A tool is selected or called with invalid arguments or assumptions.
- **incomplete_plan**: The final plan omits a required item, leg, action, or verification.
- **state_tracking_error**: The plan relies on stale or contradictory tool state.
- **premature_finalization**: The agent finalizes before checking all hard constraints.
