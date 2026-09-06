{
  "metadata_json": {
    "name": "deepplanning-constraint-check",
    "description": "Plan with explicit hard-constraint verification.",
    "when_to_use": [
      "When a task contains interacting hard constraints.",
      "Before finalizing a shopping cart or travel itinerary."
    ]
  },
  "skill_body_md": "## Workflow\n\n1. Extract every hard constraint.\n2. Track evidence after each tool call.\n3. Recheck all constraints before finalizing.\n\n## Error Avoidance\n\n- Do not treat a partial score as complete success."
}
