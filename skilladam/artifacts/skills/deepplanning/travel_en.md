---
name: planning-multi-day-domestic-trips
description: Plans multi-day domestic trips with intercity transportation, accommodation, attractions, and dining. Handles user-specified constraints on transportation timing and class, hotel amenities and ratings, attraction types and ratings, restaurant features and locations, and total budget. Use when the user requests a complete itinerary with specific preferences across multiple cities and days.
when_to_use:
  - The user requests a multi-day trip between two domestic cities with specific dates
  - The request includes constraints on transportation (departure time windows, train series, seat class, or cost preferences)
  - The request specifies hotel requirements (star rating, brand, amenities, or price range)
  - The request names specific attractions to visit or requests top-rated attractions of a certain type
  - The request specifies restaurant requirements (location proximity, rating, cuisine, features like waiting areas, or Must-Eat status)
  - The request includes a total budget constraint
---

# Planning Multi-Day Domestic Trips

## Workflow

1. **Gather transportation options**: Query all intercity transportation for both outbound and return journeys in parallel with accommodation and attraction searches. When the user specifies a departure time window, seat class, or cost preference, filter results accordingly before selecting.

2. **Identify accommodation and attractions**: Query hotels matching the user's star rating, brand, or amenity requirements. Retrieve attraction recommendations and details for all sites the user named or requested by type. When the user asks for the highest-rated attraction of a category, compare ratings across all returned options.

3. **Locate dining options**: Search for restaurants near user-specified landmarks or attractions. When the user requests a restaurant with specific features (waiting area, Must-Eat status, cuisine type), verify these attributes in the detailed results before selection. If a named location is not found, search near a related attraction rather than abandoning the requirement.

4. **Compute routes and assemble the itinerary**: Retrieve coordinates for all selected locations, then query travel times and costs between every consecutive pair of activities. Structure each day with continuous time blocks, ensuring transportation segments connect all locations without gaps. On arrival and departure days, adjust activity density to match effective sightseeing time.

5. **Validate budget and constraints**: Sum all transportation, accommodation, meal, and attraction costs. Verify the total does not exceed the user's budget and that all user-specified constraints (transportation timing, hotel amenities, attraction types, restaurant features) are satisfied in the final plan.

## Error Avoidance

- Do not select transportation options outside the user's specified time window or class preference, even if they are cheaper or faster.
- Do not assume a hotel meets amenity requirements without confirming the specific features in the query results.
- When a user requests the highest-rated attraction or restaurant of a type, compare all returned options rather than selecting the first result.
- Do not place meals outside restaurant operating hours or attraction visits outside opening hours.
- Do not calculate transportation costs incorrectly — verify whether the price is per person or per vehicle and multiply by the correct passenger or vehicle count.
- When a named location is not found, search near a related attraction or landmark rather than omitting the user's requirement entirely.
- Do not schedule overlapping activities or leave unexplained time gaps between consecutive activities.
- On arrival days, always verify meal requirements based on arrival time (morning → both lunch and dinner; afternoon 10:00–15:00 → dinner required; evening after 15:00 → no meals or one dinner only). Do not skip required meals even when the arrival day is busy with check-in and sightseeing.
- When selecting the top-N highest-rated attractions, query details for ALL recommended attractions before ranking. Ratings are often tied, so a late-discovered attraction may share the same top rating and must be included in the selection.
- Before scheduling any attraction or meal, verify that the entire scheduled time window (start time to end time) falls within the entity's operating hours. If an attraction closes at 17:00, the visit must end by 17:00 or earlier. If a restaurant opens at 11:00, the meal cannot start before 11:00.
- When `search_location` returns no coordinates for a hotel, do not use coordinates from any other source (e.g., nearby restaurants or attractions). Instead, re-check the exact hotel name from the `query_hotel_info` result and retry `search_location` with the precise name. If it still fails, select a different hotel.
- Do not schedule the same attraction or restaurant on multiple days of the trip. Once an attraction or restaurant appears in the itinerary, it cannot be visited again on a different day.