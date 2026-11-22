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
- **Merge pass.** After new repos are sorted, one small extra AI call looks only at the category names (with a few example repos) and merges near-duplicates, such as "CLI Tools" and "Developer Tools". Names from earlier runs win, so your GitHub lists keep their names. If the call fails, the run keeps its categories.
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

1. **AWS credentials: use `aws login`, not access keys.** `aws login` (AWS CLI 2.32+) signs you in through the browser and gives short-lived credentials that refresh on their own, so there is no secret key on disk that could leak:

   ```bash
   aws login --profile admin     # sign in as an IAM user that may call Bedrock (and run Terraform)
   ```

   boto3, which starsort uses, can only read `aws login` sessions with the extra `botocore[crt]` package. Instead of adding it, add a profile to `~/.aws/config` that asks the AWS CLI for the credentials:

   ```ini
   [profile starsort-cli]
   credential_process = aws configure export-credentials --profile admin --format process
   region = us-west-2
   ```

   Then use `AWS_PROFILE=starsort-cli` for starsort. When the session expires, run `aws login --profile admin` again.

   The [`infra/`](infra/) folder has Terraform for a monthly budget alert (it also creates a Bedrock-only IAM user, which you don't need with `aws login`). Terraform reads `aws login` sessions from AWS provider 6.23:

   ```bash
   cd infra
   cp terraform.tfvars.example terraform.tfvars   # Windows: Copy-Item. Then set alert_email.
   AWS_PROFILE=admin terraform init -upgrade
   AWS_PROFILE=admin terraform apply
   ```

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
| `GITHUB_TOKEN` | Optional. Raises the GitHub rate limit from 60 to 5,000 requests/hour. No scopes needed for public stars. Required for `--sync-lists`: use a **classic** token with the `user` scope (fine-grained tokens can read lists but can't create them). |
| `AWS_PROFILE` | AWS profile for Bedrock, e.g. `starsort-cli` (see [Set up AI access](#option-a-amazon-bedrock-default)) |
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
starsort --user <your-username> --sync-lists --dry-run   # preview GitHub Lists changes
starsort --user <your-username> --sync-lists             # create/update your GitHub star Lists
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
| `--sync-lists` | Also put each repo in a GitHub star List named after its category |
| `--dry-run` | With `--sync-lists`: show what would change, change nothing |

### GitHub star Lists

`--sync-lists` mirrors the categories into GitHub's own **Lists** on your Stars tab, so you get the folders on GitHub itself, not only in Markdown.

- It only works for **your own** account: `GITHUB_TOKEN` must belong to `--user`, and it must be a **classic** token with the `user` scope. Fine-grained tokens can read lists but GitHub rejects them for creating lists.
- Lists it creates get the description "Managed by starsort". Repos move between those lists when their category changes.
- **Your own lists are never touched.** If a list with the same name as a category already exists, starsort adds repos to it but never removes anything from it.
- "Other" doesn't get a list.
- **No empty lists.** A list is created only when its first repo goes in, and after a complete sync starsort deletes its own lists ("Managed by starsort") that ended up empty. Your own lists are never deleted.
- **GitHub limits how fast lists can change** (about 80 changes a minute and 500 an hour). If a sync hits the limit, it stops cleanly: run it again later to finish.
- Run with `--dry-run` first to see the plan, including the empty lists it would delete.

**Publish it:** create a repo (e.g. `awesome-stars`), run starsort with `-o README.md` inside it, then commit and push. GitHub renders the list on the repo page.

## Cost

starsort only sends repo names, descriptions, topics and languages. A full run over about 1,000 repos is roughly 100k tokens, which is around $1 or less on Claude Sonnet- or Opus-class models. Later runs only pay for newly starred repos, and a run with nothing new makes no AI call. Check current prices on the [Amazon Bedrock](https://aws.amazon.com/bedrock/pricing/) or [Anthropic](https://www.anthropic.com/pricing) pricing pages.

## Web app

starsort also runs as a hosted web app, so people can try it without installing anything: sign in with GitHub, start a run, get the list. It is a hands-on project for learning real AWS services.

```text
Browser ──> CloudFront ──┬─ /*     ──> S3 (Angular site)
                         └─ /api/* ──> API Gateway ──> API Lambda ──> DynamoDB
                                                           │ (async)
                                                           └──> Worker Lambda ──> GitHub + Bedrock
```

- **Sign in with GitHub (OAuth).** The GitHub token is used once to read your public profile, then dropped. The session is a signed, HttpOnly cookie.
- **Runs are background jobs.** The API queues the job, and the worker sorts your stars with the same code as the CLI and saves the result in DynamoDB. The dashboard shows live progress, then the Markdown to copy or download.
- **Sync to your GitHub Lists from the browser.** GitHub asks you once more for permission to edit your profile (the `user` scope). That token is used for one sync only and revoked as soon as it ends. You can make the new lists private, and a sync changes at most 300 repos to stay under GitHub's limits.
- **Some organizations block apps like starsort** (GitHub's "OAuth App access restrictions", e.g. `awslabs`). The web app can't read or move their repos, so it skips them and says which organizations it skipped. The CLI uses your own token instead of an app, so it can still sort them.
- **Free trial limits:** 1 run per GitHub account per week, up to the 500 newest stars, and a monthly AI spend cap that pauses runs for everyone.
- **One address.** CloudFront serves the site and forwards `/api/*` to API Gateway, so the session cookie works without CORS.
- **No secrets in code or Terraform state.** They live in SSM Parameter Store. All infrastructure is Terraform in [`infra/web/`](infra/web/).

**Deploy your own** (needs an AWS account, a GitHub OAuth App, Terraform, Node 24 and PowerShell for the scripts):

1. In the SSM Parameter Store console, create two `SecureString` parameters: `/starsort/web/github-client-secret` (your OAuth App's client secret) and `/starsort/web/session-secret` (a long random string). Using the console keeps the secrets out of your shell history.
2. Build the worker's dependencies layer: `./scripts/build_layer.ps1`
3. In [`infra/web/variables.tf`](infra/web/variables.tf), set `github_client_id` and `unlimited_logins` to your own, then run `terraform init` and `terraform apply` in `infra/web` (with `AWS_PROFILE=admin`).
4. Set the OAuth App's callback URL to the `oauth_callback_url` output.
5. Build and upload the site: `npm ci` and `npx ng build` in `frontend/`, then `./scripts/deploy_frontend.ps1`. Open the `site_url` output.

## Development

```bash
uv sync
uv run pytest
```

The tests mock both the GitHub API (`httpx.MockTransport`) and the agent, so they need no network, AWS account or API key. Frontend tests (Vitest):

```bash
cd frontend
npm ci
npx ng test --watch=false
```

To work on the site locally, `npx ng serve --proxy-config proxy.conf.json` forwards `/api` to the deployed site.

### Evals: is the AI sorting well?

Unit tests can't check AI output, because the model words things a little differently every run. The [`evals/`](evals/) folder scores it instead, with [Strands Evals](https://strandsagents.com/docs/user-guide/evals-sdk/index.md):

- **Golden set:** [`evals/golden_repos.json`](evals/golden_repos.json) has 33 real repos, each tagged with the group it belongs to.
- **Task:** the real categorizer sorts them twice: in one batch, and in batches of 8 (like a big account, where category names can drift between batches).
- **Checks:** every repo gets a category, 5–15 categories, repos of the same group land together and different groups stay apart (category names don't matter), and an AI judge looks for near-duplicate categories such as "AI Agents" vs "AI Coding Assistants".

```bash
AWS_PROFILE=starsort-cli uv run python -m evals.run_evals                                    # print the scores
AWS_PROFILE=starsort-cli uv run python -m evals.run_evals --save evals/results/baseline.json # keep them
AWS_PROFILE=starsort-cli uv run python -m evals.run_evals --model <bedrock-model-id>         # compare models
AWS_PROFILE=starsort-cli uv run python -m evals.run_evals --no-merge                         # without the merge pass
```

The judge always uses Sonnet 4.6 (`--judge-model`), so runs with different models are graded the same way. Each run costs a few cents of Bedrock. Evals are for developers only: they are never deployed, and the web app never calls the judge. Scores move a little between runs, so compare big differences, not small ones. Saved results are in [`evals/results/`](evals/results/).

```text
src/starsort/
  cli.py          Typer CLI: wires everything together
  service.py      the whole flow as plain functions, shared by the CLI and the web worker
  github.py       fetch starred repos (pagination, token, stop at first known repo)
  github_lists.py sync categories to GitHub star Lists (GraphQL API)
  cache.py        JSON cache of repos and their categories
  categorizer.py  batching, prompt, name cleanup, category limit, incremental logic
  providers.py    Strands models (Bedrock / Anthropic) and the structured-output agent
  markdown.py     awesome-list rendering
  models.py       Pydantic models
  web/            Lambda code: api.py (HTTP API), worker.py (runs jobs), auth.py, sessions.py, jobs.py, limits.py
frontend/         Angular site: landing page and dashboard
infra/            Terraform: budget alert (and a Bedrock-only IAM user)
infra/web/        Terraform: the web app (Lambda, API Gateway, DynamoDB, S3, CloudFront)
scripts/          build_layer.ps1 (worker dependencies), deploy_frontend.ps1 (upload the site)
evals/            categorizer evals: golden set, scoring, Strands Evals runner, saved results
tests/            pytest suite
```

**Strands docs in your editor:** the repo ships a `.mcp.json` that connects the Strands Agents docs MCP server (`strands-agents-mcp-server`, run with `uvx`). MCP-aware tools like Claude Code pick it up automatically, so coding assistants can look up the current Strands API instead of guessing.

## Roadmap

- [x] Sync categories to GitHub's native star **Lists**
- [ ] GitHub Action that re-runs weekly and commits the updated list
- [x] Hosted web version: GitHub sign-in, dashboard and free-trial limits
- [x] Start a run and see its progress and result in the browser
- [x] Sync GitHub star Lists from the web app, with automatic cleanup of empty lists
- [x] Evals that score how well the AI sorts
- [ ] Merge near-duplicate categories (measured with the evals)
- [ ] CI/CD with GitHub Actions

## License

[MIT](LICENSE)
