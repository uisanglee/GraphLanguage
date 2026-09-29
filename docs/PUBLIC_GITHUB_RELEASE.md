# Public GitHub release checklist

This repository publishes source code, schemas, prompts, examples, tests, experiment definitions,
and aggregate corpus statistics. It intentionally excludes downloaded benchmark records, generated
model requests, model weights, raw experiment outputs, credentials, and locally supplied PDFs.

Run all commands from the repository root.

## 1. Inspect the candidate release

```bash
git status --short --ignored
python3 -m unittest discover -s tests -v
```

The LiveCodeBench corpus-wide classification test is skipped when the downloaded corpus is absent.
All remaining unit tests must pass.

Confirm that generated and local-only files are ignored:

```bash
git check-ignore -v \
  data/normalized/swebench.jsonl \
  data/qwen/nl_to_graphdsl.jsonl \
  outputs/qwen7b-parsel-graphdsl-nodes-v2 \
  references/NeurIPS-2023-parsel-algorithmic-reasoning-with-language-models-by-composing-decompositions-Paper-Conference.pdf
```

## 2. Register upstream Parsel as a submodule

The existing `third_party/parsel` directory is already a clean clone of the official repository.
Register it in the parent repository and retain the reviewed upstream commit:

```bash
git submodule add --force https://github.com/ezelikman/parsel.git third_party/parsel
git -C third_party/parsel checkout bc1687cab9e8a79249f0d3e3cb4f5826dba39a95
git submodule status
git -C third_party/parsel status --short
```

The final command must print nothing. Do not commit modifications inside the submodule.

## 3. Stage and audit the first commit

```bash
git add .
git diff --cached --check
git status --short
git diff --cached --stat
```

Verify that no generated corpus, result, weight, credential, or PDF is staged:

```bash
git ls-files | grep -E '^(data/(normalized|qwen|raw)/|outputs/|runs/|models/|checkpoints/|references/.*\.pdf$)' && echo 'STOP: excluded files are staged' || echo 'OK: excluded files are absent'
```

Search the staged snapshot for common credential formats. Any match must be inspected before
continuing; placeholder variable names such as `OPENAI_API_KEY` are allowed, but real values are not.

```bash
git grep --cached -n -E 'sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH|EC|DSA) PRIVATE KEY' -- . ':!docs/PUBLIC_GITHUB_RELEASE.md' && echo 'STOP: inspect possible secret' || echo 'OK: no common secret pattern found'
```

Review the full staged patch, then commit it:

```bash
git diff --cached
git commit -m "Initial public GraphDSL research implementation"
```

## 4. Create and push the public repository

Install and authenticate GitHub CLI if necessary:

```bash
brew install gh
gh auth login
```

Replace `YOUR_REPOSITORY_NAME` with the desired GitHub repository name. The command creates the
public repository under the authenticated account, configures `origin`, and pushes `main`.

```bash
gh repo create YOUR_REPOSITORY_NAME --public --source=. --remote=origin --push
```

Verify the published repository and submodule:

```bash
git remote -v
gh repo view --json nameWithOwner,visibility,url
git ls-remote origin HEAD
```

## 5. Verify a clean public clone

Use a fresh temporary clone to ensure the repository does not depend on ignored local files:

```bash
release_check_dir="$(mktemp -d)"
git clone --recurse-submodules "$(git remote get-url origin)" "$release_check_dir/GraphLanguage"
cd "$release_check_dir/GraphLanguage"
python3 -m unittest discover -s tests -v
```

Keep the temporary directory until the verification finishes. It can then be removed manually.

## 6. Normal update workflow

```bash
git switch main
git pull --ff-only origin main
git status --short
git add <reviewed-files>
git diff --cached --check
git diff --cached
git commit -m "Describe the change"
git push origin main
```

Never use `git add -f` to bypass the ignore rules for datasets, outputs, weights, credentials, or
PDFs. If a secret was committed, deleting the file in a later commit is insufficient: revoke or
rotate the secret immediately and remove it from Git history before publishing.

## Licensing note

A public repository without a `LICENSE` file is visible but does not grant general reuse rights.
Choose a license deliberately before inviting external reuse. Do not copy the Parsel license into
the parent project; the submodule retains its own upstream licensing and history.
