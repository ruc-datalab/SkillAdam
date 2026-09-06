# Benchmark Data Preparation

## Overview

SkillAdam does not bundle raw benchmark data, images, workbooks, game files,
answer collections, or run outputs. The data preparer defaults to a plan;
add `--execute` to download or copy data into an external directory.

```bash
python scripts/prepare_benchmark_data.py \
  --benchmark searchqa \
  --destination <external-data-root>
```

The plan reports sources, revisions, license requirements, layout, preparation
steps, and a stable `plan_sha256`. Planning does not access the network or
create directories. Execution requires an absent or empty destination.
Downloads are staged in a sibling directory and installed only after
checksum, split, dependency, and adapter validation. Existing data is not
overwritten.

OfficeQA requires gated access. SearchQA and LMB require `--accept-terms`
because their data redistribution terms are not established by the source
metadata. Review the terms yourself; these options do not grant data rights.

### Recipe Identity

| Benchmark | Plan SHA-256 |
|---|---|
| ALFWorld | `a47dedccd0aba50a3682c6e0e0d628a20dfbf90211ecce2f69847a4a6aa7dec5` |
| DeepPlanning | `290dcb180c19f04dc2c2ee1271b7beab17270a6f7cc03f805d0d3ef7fc7bca17` |
| DocVQA | `4a9c92df0e2f977426f660b7e7b6594f91efb85f918c10c9f96c3e169120abd7` |
| LMB | `61cd1600802ad2e76c9568410f61ef0b7b82c15dcaeebc5e4160ca4d9c34e40b` |
| OfficeQA | `498096e2d120f5ae0ee94f660b5b5787ee004982ac31650b1ac0531708b977c6` |
| SearchQA | `aa050addf27b2abd9515d3655af41d5c453ff6ba37653baf66f06d5d199b743e` |
| SpreadsheetBench | `da7caa6d8ab5bcd9dcdc86807c50182f3a33e967e610b1eaffe5141b1aa97586` |

These hashes identify preparation recipes, not downloaded archives. Archive
checksums, where available, are validated separately against pinned sources.

## Sources and Layouts

| Benchmark | Pinned source | Access and license | Adapter layout |
|---|---|---|---|
| ALFWorld | `alfworld==0.4.2` | Package: MIT; game payload redistribution rights are not established here | `split_manifest.json`, `{train,val,test}/items.json`, 191 selected gamefiles |
| DeepPlanning | Qwen-Agent `31a4d36d123688581a9e9744427272b33ce940e0`; `Qwen/DeepPlanning` `213876cce679f993a476d01042e13d111c0e3648` | Both pinned sources declare Apache-2.0 | `deepplanning_runtime_manifest.json`, Qwen-Agent checkout, four query/database scopes |
| DocVQA | `lmms-lab/DocVQA` `539088ef8a8ada01ac8e2e6d4e372586748a265e`, config `DocVQA`, upstream validation | Dataset card: Apache-2.0 | `split_manifest.json`, `{train,val,test}/items.json`, `images/<question-id>.png` |
| LMB | `LiveMathematicianBench/LiveMathematicianBench` `b72450f6ce96c26158d64d945a5d31ef7727be41` | Review dataset terms; redistribution rights are not established here | `raw/data/<month>/qa_<month>_final.json`, optional `{train,val,test}/items.json` |
| OfficeQA | `databricks/officeqa` `8ecbf18d3833daf4750a903d14963e4c4c1d4cd8` | Gated; CSV: CC-BY-SA-4.0; conversion code: Apache-2.0; Treasury corpus: public domain | `raw/officeqa_full.csv`, split files, 285 pairs under `raw/treasury_bulletins_parsed/{transformed,jsons}` |
| SearchQA | `lucadiliello/searchqa` `c1a979068ba118d85467179b704031d113d689cc` | Review dataset terms; code BSD-3-Clause does not cover Jeopardy! content | `searchqa_manifest.json` |
| SpreadsheetBench | `KAKA22/SpreadsheetBench` `ab0b742b0fc95b946f212d80ac7771b5531272e4` | CC-BY-SA-4.0 | `data/dataset.json`, `data/spreadsheet/<id>/`, `{train,val,test}/items.json` |

## Paper Split Profiles

| Benchmark | Train | Validation | Test | Notes |
|---|---:|---:|---:|---|
| ALFWorld | 39 | 18 | 134 | `val` maps to validation; test is out-of-distribution |
| DocVQA | 107 | 53 | 374 | Project-specific split of a ten-percent upstream validation subset; not the official test set |
| SearchQA | 400 | 200 | 1,400 | `val` normalizes to `validation` |
| SpreadsheetBench | 80 | 40 | 280 | Verified-400 |
| OfficeQA | 50 | 24 | 172 | `val` maps to validation |
| LMB | 35 | 18 | 124 | Deterministic `2:1:7` split over four monthly files |
| DeepPlanning | 78 | 42 | 120 | Totals across four independently split scopes |

Training uses `--split train`, but the optimization pool depends on the
protocol. The custom CLI defaults to separate validation. The frozen paper
profile combines train and selection for SkillAdam and uses a same-batch
gate; SkillOpt keeps train and selection separate. Neither method optimizes
on the test split. See [paper settings](reproducibility/main_results.md).

SearchQA prohibits injecting verbatim question/answer pairs. Its selected
skill contains optimizer-learned answer-form examples and is not zero-shot.

ALFWorld resolves relative gamefiles below `ALFWORLD_DATA`. Preparation
copies only the 191 files named by the split profile. Missing files and
symlinks escaping the root are rejected before provider requests.

SpreadsheetBench resolves distinct input/golden regular `.xlsx` files under
the data root. It copies only the input into each attempt directory; the
parent process uses the golden workbook for grading and does not send it to
the model. The default `local-subprocess` mode is not a security sandbox:
generated code has the current user's filesystem permissions. Optional
Docker/Podman mode uses a separately supplied image and restricted mounts.

DeepPlanning uses `skilladam-paper-240-v1`: three shopping levels and
`travel_en`, not the full upstream multilingual collection. The runtime must
contain 240 canonical IDs. Queries, databases, required scripts, Qwen-Agent
HEAD, symlink boundaries, and the bridge protocol are checked before client
creation. See `configs/deepplanning_runtime_manifest.example.json`.

## One-Command Preparation

Install the target benchmark's requirements, then inspect its plan:

```bash
python scripts/prepare_benchmark_data.py \
  --benchmark <benchmark> \
  --destination /absolute/external/data/<benchmark>
```

Add `--execute` to acquire ALFWorld, DocVQA, SpreadsheetBench, or DeepPlanning.
SearchQA and LMB also require `--accept-terms`. OfficeQA requires
`--accept-terms` and an authorized `HF_TOKEN`. Any `--cache-dir` must also be
outside the repository. Token values are not printed or recorded.

```bash
python scripts/prepare_benchmark_data.py \
  --benchmark searchqa \
  --destination /absolute/external/data/searchqa \
  --accept-terms \
  --execute
```

The packaged `skilladam-main-results-v1` profiles contain IDs and relative
paths, not dataset payloads. Preparation validates 534 DocVQA IDs and images,
2,000 SearchQA IDs, SpreadsheetBench's fixed archive checksum and workbooks,
and ALFWorld's 191 gamefiles. DeepPlanning creates the pinned Qwen-Agent
checkout, downloads four archives, and verifies both revisions and all 240
cases before installing the runtime.

OfficeQA preparation downloads the gated CSV, the 50/24/172 split, and only
the 285 Treasury Bulletin text/parsed-JSON pairs referenced by those 246
cases. Main-result preflight rejects missing or empty text, invalid JSON, and
symlinks before creating a provider client.

## Prepare Existing Local Data

For the six non-DeepPlanning benchmarks, copy an authorized source into a new
external destination without network access:

```bash
python -m skilladam materialize-data \
  --benchmark <benchmark> \
  --source-root <authorized-source-root> \
  --destination <external-empty-data-root>
```

The source is never modified, moved, or deleted. The destination must be
outside the Git repository and absent or empty. Path escapes, source or
destination symlinks, and overlapping directories are rejected.
`materialization_manifest.json` records split counts, file counts, and a tree
content SHA-256, without source absolute paths.

Normalization rules:

- ALFWorld: validate the 39/18/134 relative-gamefile profile and 191 files.
- DocVQA: normalize image fields to `images/<filename>`; validate 107/53/374
  cases and every image.
- SearchQA: normalize `val` to `validation`; validate 400/200/1400 cases.
- SpreadsheetBench: normalize string or non-boolean integer IDs to strings;
  validate 80/40/280 cases and all init/golden workbooks.
- OfficeQA and LMB: validate 50/24/172 and 35/18/124 cases, respectively, and
  all adapter dependencies.

Materializer `--dry-run` performs the same conversion and validation in a
temporary sibling directory and retains no destination. It reads and
temporarily copies real files, so allow enough disk space.

DeepPlanning uses its runtime manifest instead of this materializer. For
local validation through `prepare_benchmark_data.py`, set `--source-root`
and `--destination` to the same existing runtime. This mode validates but
does not copy or rewrite it. Both code/data revisions, four scopes, 240 cases,
required sources, and file digests must match.

## Validate Prepared Data

After preparation, check adapter loading without a model call:

```bash
python -m skilladam evaluate \
  --benchmark <benchmark> \
  --method baseline \
  --split test \
  --data-root <data-root> \
  --backend fixture \
  --model fixture-model \
  --output-dir <new-output-dir> \
  --dry-run
```

This checks planning only. The `fixture` backend does not run a real model or
benchmark environment. Use an explicit `module:factory` backend for real
execution and review its configuration and costs first.

## Safety and Access

1. Preparation planning performs no network access or writes; execution is explicit.
2. Gated access and dataset terms remain the user's responsibility.
3. Destination and cache are external; a destination must be absent or empty.
4. Archive extraction rejects absolute paths, `..`, links, and special files.
5. Revisions, available archive checksums, split profiles, and adapter counts
   must match.
6. Local preparation copies or validates data without modifying the source.

Preparation does not clear existing caches, remove old datasets, or write
benchmark payloads into the repository.
