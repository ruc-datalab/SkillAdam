# SkillAdam

When the user asks to optimize an Agent Skill, use the `skilladam_*` MCP tools
and follow the `skilladam-optimize` Skill. Research and submit the explicit task
manifest in the host session. Call `skilladam_discover_history` before building
the first manifest and use only validated local-history references. Keep manifest validation, evaluation execution,
patch selection, candidate validation, and gate decisions in the shared
SkillAdam workflow. Do not edit the target Skill directly as a substitute for
that workflow.
