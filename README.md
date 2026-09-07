<p align="center">
  <img src="assets/logo-readme.svg" alt="SkillAdam" width="480">
</p>

# SkillAdam: Better Skills for Your AI Agent

[![Paper](https://img.shields.io/badge/Paper-PDF-b31b1b.svg)](assets/SkillAdam.pdf)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code Stars](https://img.shields.io/github/stars/ruc-datalab/SkillAdam?style=social&label=Code%20Stars)](https://github.com/ruc-datalab/SkillAdam)

**Give SkillAdam a skill and tell it what you want to improve.** It tests your `SKILL.md` on relevant tasks, learns from the results, and checks proposed changes before updating the file.

**Supported platforms: Codex · Claude Code · Cursor Agent · GitHub Copilot.**

- **Improve skills for your workflow.** Describe your goal and provide examples of tasks your skill should handle.
- **Learn from earlier attempts.** Use feedback from previous revisions to guide the next improvement and address recurring mistakes.
- **Make measured changes.** Adapt the scope of each revision and keep changes that pass evaluation.
- **Stay in your preferred agent.** Start an optimization in the agent you already use, with the option to review individual edits.

<p align="center">
  <a href="assets/skilladam-framework.pdf">
    <img src="assets/skilladam-framework.png" alt="SkillAdam framework: task rollouts, optimization memory and adaptive edits, followed by skill updates." width="900">
  </a>
</p>

<p align="center">
  <a href="#demo">Demo</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#citation">Citation</a>
</p>

## News

- **[2026.09]** SkillAdam is released with integrations for Codex, Claude Code, Cursor Agent, and GitHub Copilot.

## Demo

*Demo video coming soon.*

<!-- Replace this placeholder with the demo video URL when available. -->

## Quick Start

### Requirements

- **Python 3.10+**, with `pip` and `venv`, and **Git**.
- Your chosen agent's CLI, installed, signed in, and available on your `PATH`: `codex`, `claude`, `cursor-agent`, or `copilot`.
- For Copilot's default VS Code integration, the **`code` CLI** is also required. For Copilot CLI only, pass `--skip-vscode-registration` to its installer.

Cursor requires the **Cursor Agent CLI**; the desktop app alone is insufficient.

The default setup uses your existing agent sign-in and needs no local GPU, Docker, or separate model API key. Model calls consume your platform account's quota, and skill and task content is sent to the configured model provider.

### Installation

Clone this repository:

```bash
git clone https://github.com/ruc-datalab/SkillAdam.git
cd SkillAdam
```

Run **one** installer for your platform from the repository root:

| Platform | macOS / Linux |
|---|---|
| Codex | `./integrations/codex/install.sh` |
| Claude Code | `./integrations/claude-code/install.sh` |
| Cursor Agent | `./integrations/cursor/install.sh` |
| GitHub Copilot | `./integrations/github-copilot/install.sh` |

<details>
<summary>Windows PowerShell</summary>

| Platform | Command |
|---|---|
| Codex | `.\integrations\codex\install.ps1` |
| Claude Code | `.\integrations\claude-code\install.ps1` |
| Cursor Agent | `.\integrations\cursor\install.ps1` |
| GitHub Copilot | `.\integrations\github-copilot\install.ps1` |

</details>

The installer sets up a dedicated Python environment and registers SkillAdam with your agent. Restart your agent after installation; in VS Code, use **Developer: Reload Window**.

For more installation options, see the [platform guide](integrations/README.md#one-step-installation).

## Usage

### Improve Your Skill

Open the workspace containing your skill and select **`skilladam-optimize`** through your agent's skill picker or invocation mechanism. Give it the skill path and your goal:

```text
Use SkillAdam to optimize /absolute/path/to/SKILL.md for writing concise,
actionable code reviews that catch correctness issues and edge cases.
```

Your agent prepares relevant tasks and scoring rules. SkillAdam tests the current skill, proposes changes, and evaluates the revised version. **By default, proposed edits are selected automatically; your skill file is updated only when the revised version passes validation.**

You can make your request more specific by describing a recurring problem or including a task example:

```text
This skill often produces long explanations without a clear recommendation.
Focus on making each review comment identify the problem, explain its impact,
and suggest a concrete fix.
```

SkillAdam currently optimizes **one existing `SKILL.md` at a time**. Include any context needed to evaluate your examples: supporting skill resources and workspace files are not automatically available during test runs. See [task guidance](integrations/README.md#tasks-and-evaluation-signals) for details.

### Choose Which Changes to Apply

To review proposed edits yourself, include this in your request:

```text
Before applying changes, show me the proposed edits and let me choose.
```

You can accept or reject individual edits. SkillAdam then validates the selected changes before updating your skill.

### Continue an Interrupted Run

Ask your agent to resume using the same run directory:

```text
Resume the SkillAdam optimization in /absolute/path/to/the/run-directory.
```

Keep your skill in version control so you can review its history or undo changes.

## Citation

*BibTeX citation coming soon.*

<!-- Replace this placeholder with the paper's official BibTeX citation. -->

## License

SkillAdam is released under the [MIT License](LICENSE). Copyright (C) 2026 Tencent. All rights reserved. Third-party attribution is preserved in [NOTICE](NOTICE).
