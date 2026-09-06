## Workflow

1. **Parse the goal**: Identify the exact target object class, required quantity, required transform (heat/cool/clean) if any, the transform appliance, and the final destination receptacle or interaction (desklamp for examine-in-light tasks). Treat the destination as the final target, not as the first place to search unless it is also a likely source.

2. **Search systematically using a visited ledger**: Before every search action, recall which exact receptacle instances have already been observed and found empty. Never revisit an instance that lacked the target. Visit high-probability locations first, opening closed containers when needed:
   - Food items (apple, egg, tomato, bread, lettuce, potato): fridge, countertops, dining tables, then cabinets and shelves.
   - Cookware and tableware (pan, pot, plate, bowl, mug, cup, knife, spatula, fork, spoon, butterknife, ladle): countertops, dining tables, stoveburners, sinkbasins, then drawers and cabinets — alternate between categories instead of exhausting all drawers or all cabinets.
   - Cleaning and hygiene items (dishsponge, cloth, soapbar, spraybottle, toiletpaper): sinkbasins, bathtub basins, countertops, carts, shelves, toilet-adjacent areas, then cabinets and garbagecans.
   - Small personal and media items (book, CD, keychain, alarmclock, pen, pencil, creditcard, remotecontrol, watch): desks, dressers, sidetables, coffeetables, sofas, TV stands, shelves, then drawers.
   - Saltshaker and condiments: dining tables, countertops, shelves, then cabinets and drawers.
   After 2–3 misses in one furniture family, rotate to another family. When all likely families are exhausted, visit any remaining unvisited admissible `go to` target before restarting any already-searched instance.

3. **Take the exact object immediately**: When the target appears in an observation, pick it up right away. Do not substitute similar items (mug ≠ cup, pan ≠ pot, knife ≠ butterknife).

4. **For examine-in-light tasks**: After taking the exact object, go to a desklamp and `use desklamp` while holding the object. No placement is needed.

5. **Apply transforms before final placement**: Carry the object to the correct appliance and perform the transform before going to the destination: `take [object] → go to [appliance] → heat/cool/clean [object] with [appliance] → go to [destination] → move [object] to [destination]`. Use microwave for heating, fridge for cooling, sinkbasin for cleaning.

6. **Place at the destination decisively**: Navigate to the destination receptacle, open it if closed, then `move` the object into it. For two-object tasks: after placing the first object, if another instance was already seen at a specific location, return directly there. Otherwise continue searching from unvisited locations only — do not take the first placed object back out and re-place it.

## Error Avoidance

- **Hard visited-instance lockout**: once a receptacle instance has been observed without the target, do not go back to it while still searching. Only return to a location after picking up the target (for transform or delivery).

- **No search restart**: do not restart a search sequence from instance 1 of a class that has already been checked. If all instances of countertops have been visited, move to cabinets or drawers, not back to countertop 1.

- **Broaden after 2–3 misses**: if 2–3 instances of the same type are empty, switch to a completely different furniture family or any unvisited admissible location rather than continuing the same type.

- Do not confuse the destination with the source. For "put a clean [object] in [receptacle]," find [object] first, clean it, then go to [receptacle].

- Do not place or examine at the destination before completing a required transform.

- Do not carry the wrong object class. If the carried item does not match the goal noun, drop it and resume searching.

- For two-object tasks: after placing the first object, do not take it back out. The first placed object is done; search for the second instance at new locations only.
