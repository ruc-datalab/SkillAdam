<p align="center">
  <img src="assets/logo-readme.svg" alt="SkillAdam" width="480">
</p>

# SkillAdam: Better Skills for Your AI Agent

[![arXiv](https://img.shields.io/badge/arXiv-2609.08944-b31b1b.svg)](https://arxiv.org/abs/2609.08944)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](pyproject.toml)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Code Stars](https://img.shields.io/github/stars/ruc-datalab/SkillAdam?style=social&label=Code%20Stars)](https://github.com/ruc-datalab/SkillAdam)
[![feishu](https://img.shields.io/badge/Feishu-%E5%8A%A0%E5%85%A5RUC--DataLab%E4%BA%A4%E6%B5%81%E7%BE%A4-black?logo=lark&logoColor=00D6B9)](./assets/feishu.jpg)

**Give SkillAdam a skill and tell it what you want to improve.** It tests your `SKILL.md` on relevant tasks, learns from the results, and checks proposed changes before updating the file.

**Supported platforms: Codex 路 Claude Code 路 Cursor Agent 路 GitHub Copilot.**

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
  <a href="#demo">Demo</a> 路
  <a href="#quick-start">Quick Start</a> 路
  <a href="#usage">Usage</a> 路
  <a href="#citation">Citation</a>
</p>

## News

- **[2026.09.08]** Our paper, [SkillAdam: Stable and Efficient Skill Evolution for Agents](https://arxiv.org/abs/2609.08944), is now available on arXiv.
- **[2026.09]** SkillAdam is released with integrations for Codex, Claude Code, Cursor Agent, and GitHub Copilot.

## Demo

https://github.com/user-attachments/assets/a15049ee-90b5-4cda-a270-7628a60f76ed

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

If you find SkillAdam useful, please cite our [paper](https://arxiv.org/abs/2609.08944):

```bibtex
@misc{li2026skilladam,
  title={SkillAdam: Stable and Efficient Skill Evolution for Agents},
  author={Gaoyuan Li and Meihao Fan and Yizhe Liu and Shaolei Zhang and Ju Fan and Siyi Wang and Jiaheng Hou and Xudong Weng and Honghan Tian and Zang Li},
  year={2026},
  eprint={2609.08944},
  archivePrefix={arXiv},
  primaryClass={cs.AI},
  url={https://arxiv.org/abs/2609.08944}
}
```

## License

SkillAdam is released under the [MIT License](LICENSE). Copyright (C) 2026 Tencent. All rights reserved. Third-party attribution is preserved in [NOTICE](NOTICE).

## Misc

Welcome to join the [RUC-DataLab Feishu group](./assets/feishu.jpg), chat and share ideas with other users.

<p align="center" width="100%">
<img src="./assets/feishu.jpg" alt="RUC-DataLab Feishu group" style="width: 35%; min-width: 300px; display: block; margin: auto;">
</p>

If you like SkillAdam, give it a GitHub Star ⭐

[![Star History Chart](https://api.star-history.com/svg?repos=ruc-datalab/SkillAdam&type=Date&legend=top-left)](https://star-history.com/#ruc-datalab/SkillAdam&type=date&legend=top-left)
