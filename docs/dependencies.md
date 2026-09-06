# Benchmark dependencies

SkillAdam keeps its core dependency-free. Real benchmark execution is
installed one benchmark at a time from a requirements file next to that
benchmark's adapter. All commands below must be run from the repository root.

| Benchmark | Install command | Additional caller-owned runtime |
|---|---|---|
| ALFWorld | `python -m pip install -r skilladam/benchmarks/alfworld/requirements.txt` | Pinned ALFWorld game data prepared outside the repository |
| DocVQA | `python -m pip install -r skilladam/benchmarks/docvqa/requirements.txt` | Materialized images and split metadata |
| SearchQA | `python -m pip install -r skilladam/benchmarks/searchqa/requirements.txt` | Materialized split metadata |
| SpreadsheetBench | `python -m pip install -r skilladam/benchmarks/spreadsheetbench/requirements.txt` | Materialized input/golden workbooks |
| OfficeQA | `python -m pip install -r skilladam/benchmarks/officeqa/requirements.txt` | Materialized split metadata and allowlisted Treasury documents |
| LMB | `python -m pip install -r skilladam/benchmarks/lmb/requirements.txt` | Materialized split metadata |
| DeepPlanning | `python -m pip install -r skilladam/benchmarks/deepplanning/requirements.txt` | Pinned Qwen-Agent checkout, official data runtime, and bridge interpreter |

The files select extras from `pyproject.toml`; they do not duplicate package
version constraints. The six general benchmarks install the configured OpenAI
SDK boundary. Data-capable requirements install `huggingface-hub`, `pyarrow`,
and Pillow for the unified preparation command. ALFWorld pins upstream
`0.4.2`, SpreadsheetBench also installs `openpyxl` and `pandas`, and
DeepPlanning installs the official bridge dependencies plus
`huggingface-hub`. SkillOpt uses the same benchmark runtime and provider
boundary as SkillAdam, so no vendor-wide SkillOpt dependency set is installed.

## Offline dependency check

After installation, inspect one benchmark without importing its packages or
contacting a provider. It does not read credentials:

```bash
python -m skilladam check-environment --benchmark searchqa
```

The JSON report includes the Python interpreter, declared distributions,
detected versions, version-constraint status, top-level import availability,
and the exact requirements file. `dependencies_ready=true` means every
declared module is importable and satisfies the checked-in numeric constraint.
It does not mean that data, credentials, provider connectivity, or benchmark
evaluation has been checked.

SpreadsheetBench defaults to the paper-profile local-subprocess execution mode:

```bash
python -m skilladam check-environment \
  --benchmark spreadsheetbench \
  --spreadsheet-execution-mode local-subprocess
```

No Docker daemon or image is needed in that mode. Generated model code runs
with the current user's operating-system permissions, so use a disposable or
otherwise trusted machine. Provider credentials are removed from the child
environment, and every code attempt is retained below the external run output.

Container isolation is optional and must be selected explicitly:

```bash
python -m skilladam check-environment \
  --benchmark spreadsheetbench \
  --spreadsheet-execution-mode container \
  --container-engine docker
```

This checks only that the selected executable exists. The stricter runtime
preflight separately verifies the immutable image digest and security profile
before provider use.

## External tools and data

- ALFWorld preparation downloads into disposable staging and retains only the
  191 configured game files in the caller-owned external data root. The repository
  does not redistribute them.
- DocVQA needs local images but no separate OCR service in the public adapter.
- SearchQA and LMB need no external executable beyond Python; their datasets
  remain outside the repository.
- SpreadsheetBench local mode needs Python, `openpyxl`, and `pandas`. Docker or
  Podman is optional.
- OfficeQA uses bounded read-only tools implemented in the repository. Its
  one-command data preparer downloads only the 285 allowlisted Treasury
  documents and parsed JSON files needed by the frozen profile; it needs no
  database server.
- DeepPlanning requires the frozen Qwen-Agent and official data revisions.
  The dependency check cannot replace its strict Git/data/runtime preflight.

Dataset acquisition and deterministic materialization are separate from
package installation. See [Datasets](datasets.md); requirements installation
does not download benchmark payloads or write credentials.
