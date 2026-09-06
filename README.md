# SkillAdam

Make your agent skills better at the tasks you care about.
SkillAdam tests and improves an existing `SKILL.md` for your workflow.

**Supported platforms: Codex · Claude Code · Cursor · GitHub Copilot.**

[Install](#installation) · [Use](#usage) · [Docs](#documentation)

## Demo

*Demo video coming soon.*

<!-- Add the demo video here when available. Keep this section video-only. -->

## Installation

You need **Python 3.10+** and an installed, signed-in CLI for your chosen host.

```bash
git clone https://github.com/ruc-datalab/SkillAdam.git
cd SkillAdam
```

Run **one** installer for the host you use.

macOS / Linux:

```bash
./integrations/codex/install.sh
./integrations/claude-code/install.sh
./integrations/cursor/install.sh
./integrations/github-copilot/install.sh
```

<details>
<summary>Windows PowerShell</summary>

```powershell
.\integrations\codex\install.ps1
.\integrations\claude-code\install.ps1
.\integrations\cursor\install.ps1
.\integrations\github-copilot\install.ps1
```

</details>

The installer sets up the Python environment and plugin for you. To update,
first get the latest repository version, rerun the installer, and restart your
host. In VS Code, use `Developer: Reload Window`.

Copilot's default installation needs the VS Code `code` CLI. For Copilot CLI
only, add `--skip-vscode-registration`. More options and troubleshooting:
[installation guide](integrations/README.md).

## Usage

Open the workspace containing your skill. Select the installed
`skilladam-optimize` skill using your host's skill picker or invocation
mechanism, then give it the file path and your goal:

```text
Use SkillAdam to optimize /absolute/path/to/SKILL.md for <your goal>.
```

SkillAdam creates tasks and scoring rules, tests the skill, and proposes
improvements. **By default, all proposed edits are selected automatically;
the original file is updated only when validation passes.**

Want to choose the changes yourself? Include this in your request:

```text
Before applying changes, show me the proposed edits and let me choose.
```

If a run is interrupted, ask SkillAdam to resume using the same run directory.
Keep your skill in version control so you can review or undo changes.

No separate API key is needed with the default signed-in host setup.
Model usage still consumes your account's quota, and skill/task content is
sent to the configured model provider. Improvement on every task is not guaranteed.

## Documentation

- [Plugin options, platform differences, and troubleshooting](integrations/README.md)
- [Benchmark installation, data preparation, and reproduction](docs/reproducibility/quickstart.md)

## Paper

*arXiv link and citation coming soon.*

## License

[MIT](LICENSE) · Copyright (C) 2026 Tencent. All rights reserved.
Third-party license and attribution notices are preserved; see [NOTICE](NOTICE).
