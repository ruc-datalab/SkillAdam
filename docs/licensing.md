# Licensing and Attribution

## SkillAdam

SkillAdam is distributed under the [MIT License](../LICENSE), with the
copyright notice `Copyright (C) 2026 Tencent.  All rights reserved.`.
[NOTICE](../NOTICE) describes third-party attribution.
The package uses the SPDX expression `MIT` and includes the applicable
license files in wheel metadata. Third-party works retain their own notices.

This guide summarizes the distribution's license boundaries; it is not legal
advice. Review the upstream terms before using or redistributing external data.

## SkillOpt

The baseline includes material licensed by Microsoft under MIT:

- Copyright: `Copyright (c) 2026 Microsoft Corporation`.
- License: [SkillOpt LICENSE](../skilladam/baselines/skillopt/LICENSE).
- Adaptation scope: [SkillOpt NOTICE](../skilladam/baselines/skillopt/NOTICE).
- The DocVQA rollout system prompt and benchmark-specific error/success analyst
  prompts retain the upstream text.
- The scheduler, strict selection gate, slow update, memory, and optimizer
  prompt roles are implemented through provider-neutral interfaces.

Upstream provider clients, Web UI, datasets, checkpoints, and run outputs
are not bundled.

## Benchmarks and Data

Code, dataset, and document licenses can differ. A Python package or code
repository license does not establish redistribution rights for its data.

| Benchmark | License information for the configured source | Distribution boundary |
|---|---|---|
| ALFWorld | Python package metadata declares MIT | Optional dependency; game payloads are downloaded separately |
| DeepPlanning | Pinned Qwen-Agent code and Qwen/DeepPlanning dataset sources declare Apache-2.0 | The bridge uses an external, revision-verified runtime; code and data are not bundled |
| DocVQA | Pinned dataset card declares Apache-2.0 | Images are downloaded separately; use the project-specific split profile |
| LMB | Dataset redistribution rights are not established by the supplied metadata | Monthly JSON files are not bundled; review the source terms |
| OfficeQA | CSV: CC-BY-SA-4.0; conversion code: Apache-2.0; U.S. Treasury Bulletin corpus: public domain | Obtain gated access separately; CSV, answers, and parsed corpus are not bundled |
| SearchQA | Dataset page does not state clear redistribution terms; source code repository uses BSD-3-Clause | Questions, contexts, and answer rows are not bundled; code licensing does not extend to Jeopardy! content |
| SpreadsheetBench | Pinned dataset revision declares CC-BY-SA-4.0 | Workbooks are downloaded separately; retain the upstream attribution and applicable share-alike terms |

The [data guide](datasets.md) lists exact revisions, layouts, and splits.
Benchmark-specific instructions are linked from the
[research guide](reproducibility/quickstart.md#benchmark-guides).

## Credentials and Generated Files

Keep credentials, local configuration values, datasets, caches, trajectories,
checkpoints, and reports outside the distributed repository. Selected skills
and their manifests are the explicitly included reproducibility artifacts.

When distributing additions derived from third-party material, retain their
source, revision, copyright, license, and modification notices. If the terms
do not establish redistribution rights, provide acquisition instructions
rather than copying the material.
