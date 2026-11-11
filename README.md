# starsort

Turn your GitHub stars into a clean, categorized **awesome-list** README.

starsort fetches every repo you've starred, lets an AI agent ([Strands Agents](https://strandsagents.com) + Claude) group them by **what they're for**, and writes a Markdown page you can publish. Re-runs are incremental: old repos keep their category, and only new stars go to the AI.

```text
$ starsort --user ZaidShabanDev
Loaded 4 repos from cache, 1 new.
Categorizing 1 new repos (4 kept) with bedrock (us.anthropic.claude-sonnet-4-6)...
  batch 1/1 done
Wrote awesome-stars.md with 5 repos in 3 categories.
```

## Example output

```markdown
# Awesome Stars

A curated list of the repositories @ZaidShabanDev has starred, sorted into categories by starsort.

_5 repos in 3 categories. Last updated 2026-11-10._

## Contents

- [AI Coding Assistants](#ai-coding-assistants)
- [Developer Portfolios](#developer-portfolios)
- [Shaders & Graphics](#shaders--graphics)

## AI Coding Assistants

- [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) - A single CLAUDE.md file to improve Claude Code behavior, derived from Andrej Karpathy's observations on LLM coding pi...
- [awslabs/aidlc-workflows](https://github.com/awslabs/aidlc-workflows) - AI-Driven Life Cycle (AI-DLC) adaptive workflow steering rules for AI coding agents `TypeScript`

## Developer Portfolios

- [emmabostian/developer-portfolios](https://github.com/emmabostian/developer-portfolios) - A list of developer portfolios for your inspiration `Python`
- [ZaidShabanDev/portfolio](https://github.com/ZaidShabanDev/portfolio) `TypeScript`

## Shaders & Graphics

- [mattdesl/webgl-lines](https://github.com/mattdesl/webgl-lines) - some interactive content for a blog post `JavaScript`
```

## How it works

```text
GitHub REST API ──> local JSON cache ──> Strands agent (Claude) ──> Markdown
   (paginated)     (.starsort/<user>.json)   (only new repos,       (TOC + one
                                              batches of 50)         section per category)
```

- **Only the categorizing step uses AI.** Fetching, caching and writing Markdown are plain, deterministic Python.
- **Structured output.** The agent must answer as a Pydantic model (`BatchCategorization`), so results are validated JSON, not free text.
- **Batches of 50.** Large star lists (hundreds or thousands) are sent in small requests. Each batch sees the categories created so far, so names stay consistent.
- **Categories come from your data**, aiming for 5–15 plus an "Other" bucket. If there are too many, the smallest are folded into "Other". Categories from earlier runs are always kept.
- **Incremental.** Saved categories never change unless you ask (`--recategorize`). If nothing is new, no AI call is made at all.
- **Prompt-injection aware.** Repo descriptions are treated as data, never as instructions.

## Requirements

- Python 3.11+ and [uv](https://docs.astral.sh/uv/)
- Access to Claude through **one** of:
  - **Amazon Bedrock** (default), with your own AWS account
  - **Anthropic API**, with your own API key
- Optional: a GitHub token, for a higher API rate limit

You bring your own AI access, and usage is billed to your account (see [Cost](#cost)).

## Install

**As a tool (no clone needed):**

```bash
uv tool install git+https://github.com/ZaidShabanDev/starsort
```

For the Anthropic provider, install with the extra:

```bash
uv tool install "starsort[anthropic] @ git+https://github.com/ZaidShabanDev/starsort"
```

**From source (to develop):**

```bash
git clone https://github.com/ZaidShabanDev/starsort.git
cd starsort
uv sync                    # add --extra anthropic for the Anthropic provider
```

From source, put `uv run` in front of every command, e.g. `uv run starsort --user <you>`.

## Set up AI access

### Option A: Amazon Bedrock (default)

1. **AWS credentials.** The [`infra/`](infra/) folder has Terraform that creates a least-privilege IAM user (it can only call Bedrock models) and a monthly budget alert:

   ```bash
   cd infra
   cp terraform.tfvars.example terraform.tfvars   # Windows: Copy-Item. Then set alert_email.
   terraform init
   terraform plan
   terraform apply
   aws iam create-access-key --user-name starsort
   aws configure --profile starsort               # region: us-west-2
   ```

   Run Terraform with an admin profile (e.g. `AWS_PROFILE=admin`). The access key is created with the CLI on purpose, so the secret never lands in Terraform state.

2. **Model access.** In the Bedrock console (same region), open the playground, pick a Claude model and send a message once. This creates your account's agreement for that model.

3. **Pick a model your account can use.** The default is `us.anthropic.claude-opus-5-5`. If you get `AccessDeniedException ... not available for this account`, choose another one:

   ```bash
   aws bedrock list-inference-profiles --region us-west-2 \
     --query "inferenceProfileSummaries[?contains(inferenceProfileId,'anthropic')].inferenceProfileId"
   ```

   Then set it in `.env`, e.g. `STARSORT_MODEL=us.anthropic.claude-sonnet-4-6`.

### Option B: Anthropic API

```bash
uv sync --extra anthropic
```

Then in `.env`:

```text
STARSORT_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
```

The default model is `claude-opus-5-5`. Override it with `STARSORT_MODEL` or `--model`.

## Configure

Copy `.env.example` to `.env` in the folder you run starsort from. Never commit `.env`.

| Variable | Purpose |
|---|---|
| `GITHUB_TOKEN` | Optional. Raises the GitHub rate limit from 60 to 5,000 requests/hour. No scopes needed for public stars. |
| `AWS_PROFILE` | AWS profile for Bedrock, e.g. `starsort` |
| `AWS_REGION` | Bedrock region, e.g. `us-west-2` |
| `STARSORT_PROVIDER` | `bedrock` (default) or `anthropic` |
| `STARSORT_MODEL` | Model ID override |
| `ANTHROPIC_API_KEY` | Only for the Anthropic provider |

## Usage

```bash
starsort --user <github-username>                    # writes awesome-stars.md
starsort --user <github-username> -o README.md       # write somewhere else
starsort --user <github-username> --no-ai            # free and offline: group by language
starsort --user <github-username> --refresh          # refetch all stars (categories are kept)
starsort --user <github-username> --recategorize     # let the AI sort everything again
```

| Option | Description |
|---|---|
| `--user`, `-u` | GitHub username whose stars to organize (required) |
| `--output`, `-o` | Markdown file to write. Default: `awesome-stars.md` |
| `--cache` | Cache file. Default: `.starsort/<user>.json` |
| `--refresh` | Refetch all stars. Use it after unstarring repos. |
| `--provider` | `bedrock` or `anthropic` |
| `--model` | Model ID |
| `--no-ai` | Group by language instead of calling the AI |
| `--recategorize` | Forget saved categories and categorize every repo again |

**Publish it:** create a repo (e.g. `awesome-stars`), run starsort with `-o README.md` inside it, then commit and push. GitHub renders the list on the repo page.

## Cost

starsort only sends repo names, descriptions, topics and languages. A full run over about 1,000 repos is roughly 100k tokens, which is around $1 or less on Claude Sonnet- or Opus-class models. Later runs only pay for newly starred repos, and a run with nothing new makes no AI call. Check current prices on the [Amazon Bedrock](https://aws.amazon.com/bedrock/pricing/) or [Anthropic](https://www.anthropic.com/pricing) pricing pages.

## Development

```bash
uv sync
uv run pytest
```

The tests mock both the GitHub API (`httpx.MockTransport`) and the agent, so they need no network, AWS account or API key.

```text
src/starsort/
  cli.py          Typer CLI: wires everything together
  github.py       fetch starred repos (pagination, token, stop at first known repo)
  cache.py        JSON cache of repos and their categories
  categorizer.py  batching, prompt, name cleanup, category limit, incremental logic
  providers.py    Strands models (Bedrock / Anthropic) and the structured-output agent
  markdown.py     awesome-list rendering
  models.py       Pydantic models
infra/            Terraform for the Bedrock IAM user and budget
tests/            pytest suite
```

**Strands docs in your editor:** the repo ships a `.mcp.json` that connects the Strands Agents docs MCP server (`strands-agents-mcp-server`, run with `uvx`). MCP-aware tools like Claude Code pick it up automatically, so coding assistants can look up the current Strands API instead of guessing.

## Roadmap

- [ ] Sync categories to GitHub's native star **Lists**
- [ ] GitHub Action that re-runs weekly and commits the updated list
- [ ] Hosted web version, so you can use it without installing anything

## License

[MIT](LICENSE)
