## benchmark_domain_info_md

The section below is **reference material** for the task domain this skill will serve.
Read it to understand:
- The domain the skill operates in (shopping, travel, etc.) — preserve this domain vocabulary in metadata so the dispatcher can route to this skill.
- The class of constraints and typical workflow shapes used in this domain.
- Which parts are case-level detail (specific product names, prices, dates, case IDs) that must NOT leak into the skill.

Do NOT copy the material verbatim as skill rules. Instead, **distill it into operational guidance** that an agent can follow at runtime across many instances of this domain. Preserve domain terms that help the dispatcher select this skill; strip only case-level identifiers.

The material may emphasize one dominant request shape (e.g., "lowest price with coupon optimization"), but real user requests in this domain often emphasize different aspects. Where the material suggests more than one legitimate request shape, plan the skill body as a **conditional workflow** (see `Skill Writing Policy §3.1`) rather than a single linear path.

{benchmark_domain_info_md}

---

**Before you generate**, re-check every `when_to_use` item against the dispatcher litmus test:

- Can this condition be verified from the user's request text alone, before any skill execution?
- If the skill is domain-specific, does the condition carry enough domain vocabulary for the dispatcher to route to it? If the skill is genuinely cross-domain, is the condition general enough to trigger across all supported domains?
- Does the condition describe an **observable input signal**, not an execution rule or output format requirement?

If an item says what the skill must do, how it must source data, or what format it must output — move that to the body, not `when_to_use`.

**Before you finalize the body**, check:

- If the reference material admits more than one legitimate request shape in this domain, does the body start with an explicit Phase 0 classification step that routes to the right execution path?
- Does the body avoid "STOP and refuse" gates triggered by partial aspect coverage? Partial coverage is a routing signal, not a refusal condition.
