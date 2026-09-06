### Trajectory Outcome Labels

Each trajectory carries one of these labels:

```
outcome=success | hard=1, soft=1.00         — answer matched gold (ANLS ≥ 0.999)
outcome=partial | hard=0, soft=0.XX (>0)    — answer overlaps gold but ANLS < 0.999
outcome=failure | hard=0, soft=0.00         — answer unrelated to gold, or no <answer> tag
```

`hard` is the headline metric (binary near-exact match). `soft` is the
underlying ANLS score; it gives partial credit for fuzzy textual overlap and
is clipped to 0 when the normalized Levenshtein distance ≥ 0.5.

### Trajectory Summary Format

DocVQA rollouts are single-turn: one document image + question goes in,
one assistant response comes out. Each trajectory summary includes:

```
**Q**: <question text>
**Image**: <image_path basename, e.g. 12345.png>
**Topic**: <topic tag from manifest, e.g. "table/list", "form", "layout">
**Gold**: <gold answers list>
**Predicted**: <extracted answer span; "" if no <answer> tag>
**hard=<0|1>, soft=<score>**
**Response Excerpt**: <first ~300 chars of the assistant response, including
                       any reasoning or text outside <answer>...</answer>>
```

The image itself is not embedded in the summary — only its filename and
topic tag are shown. The iteration LLM cannot re-read the image; it must
rely on `Predicted` and `Response Excerpt` to infer where in the document
the agent looked, and on `Topic` to characterize the document layout.
