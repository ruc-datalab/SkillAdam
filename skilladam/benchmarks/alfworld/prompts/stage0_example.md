Below is a demonstration of the expected output format. The domain content
is illustrative only — do not transfer any of it into your output.

Input: 5 trajectory summaries from an interactive household task environment.

Output:

```json
{
  "metadata_json": {
    "name": "household-task-agent",
    "description": "Complete household tasks in an interactive text environment by navigating rooms, finding objects, performing transforms, and placing items at goal locations.",
    "when_to_use": [
      "Tasks require navigating a household environment to find, transform, and place objects at specified locations."
    ]
  },
  "skill_body_md": "# Household Task Agent\n\n## Workflow\n\n1. **Parse the goal to identify task type and required steps**: Read the task description to determine the task pattern — pick-and-place (find object, move to destination, place), pick-transform-place (find object, apply transform via appliance, move to destination, place), examine-under-light (find object, bring to lamp, use lamp), or multi-object (repeat pick-and-place twice). Identify the target object, any required transform (heat/cool/clean), the transform appliance, and the destination receptacle before taking any action.\n\n2. **Search systematically for the target object**: Navigate rooms in a consistent order rather than random exploration. At each location, check likely receptacles for the object type (e.g., food items near countertops and fridges, cleaning items near sinks). Open closed receptacles before concluding an object is absent. Once found, take the object immediately — do not leave it and come back later.\n\n3. **Execute transform before moving to destination**: If the task requires a transform, go to the correct appliance (microwave for heating, fridge for cooling, sink basin for cleaning) while holding the object. Open the appliance, put the object inside, close it (for microwave/fridge), then open again and take the object back. Do not go to the destination before completing the transform.\n\n4. **Place at destination**: Navigate to the destination receptacle. If it is a closed container, open it first. Put the object down. For multi-object tasks, return to step 2 for the second object after placing the first.\n\n## Error Avoidance\n\n- Do not place an object at the destination before performing the required transform — always complete heat/cool/clean before navigating to the goal receptacle.\n- Do not revisit rooms already fully searched — mark rooms as explored and move on to unsearched areas.\n- When a receptacle is closed, always open it before concluding the target object is not inside.\n- Do not drop the object to examine another receptacle — keep holding the target object until it reaches its destination.\n- For multi-object tasks, complete one object's full cycle (find → transform → place) before starting the second."
}
```
