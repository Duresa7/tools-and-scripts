---
name: orchestrate
description: Run an unattended multi-agent loop - plan, delegate to worker agents, verify, retry what failed, and report once at the end. The agent running this skill is the orchestrator; workers are headless Codex, Claude Code, and Antigravity (Gemini) CLI runs on the accounts listed in this skill's config.local.toml, named by engine and role (codex-builder, claude-reviewer, gemini-researcher). Use whenever the user says "/orchestrate", "orchestrate this", "delegate this", "use codex", "use gemini", "have the workers do it", names a worker like codex-builder or claude-cleaner, or wants a batch of work done by other agents while they step away. Works for any task, not only code in a repo.
---

# Orchestrate worker agents

The agent running this skill is the **orchestrator**. It plans, writes self-contained prompts, dispatches them to workers, verifies results, re-dispatches what failed, and reports once at the end. It does not do the task work itself: its job is judgment. Workers share nothing with this conversation, so every prompt must carry all the context a stranger would need.

**This runs as an unattended loop.** The user gives one instruction and expects to be left alone until the work is done. Keep looping until a stop condition fires, then deliver one report. Interrupt the user only for the reasons in **When to interrupt the user**.

Tasks can be anything: code in a repo, a one-off script, research, writing, ops work on the machine. A repo is optional.

## Step 0: load the roster

Read `config.local.toml` next to this file. It lists the engines, accounts, models, and parallel limits available on this machine. If it is missing, copy `config.example.toml` to `config.local.toml`, fill it in for the machine you are on, and confirm each CLI it names is installed (`codex --version`, `claude --version`, `agy --version`) before dispatching anything. Read `references/setup.md` when the file needs to be written from scratch.

Everything machine-specific lives in that file: home directories, which account can use which model, how many runs an account tolerates at once. The rules below never name a path or an account.

## Workers

A worker is an **engine** (the CLI) run on an **account** (a config or home directory) in a **role** (the shape of the task). Report every worker as `<engine>-<role>`, and record the account id in the ledger so a run can be traced back to the quota it spent.

| Role | The task shape | Typical output |
|---|---|---|
| `builder` | Produce or change something from a spec: code, config, a document, a data pull | Changed files, a command that now passes |
| `researcher` | Gather facts and weigh sources: read docs, compare options, summarise | A findings note with sources |
| `reviewer` | Read a change and report problems, read-only | A findings list, most severe first |
| `judge` | Decide pass/fail against a stated criterion, or pick between candidates, read-only | A verdict with the evidence that produced it |
| `cleaner` | Raise the quality of work that already functions: naming, structure, dead code, docs, consistency | A tidied change with behaviour unchanged |

The three engines and how to run each are in the references. Read the one you are about to dispatch to; each holds the exact command, the model and effort ladder, and the engine's quirks.

- `references/codex.md`: OpenAI Codex CLI (`codex exec`). Fast, cheap, strong on mechanical and clear-spec work. Round-robin across Codex accounts.
- `references/claude.md`: Claude Code CLI (`claude -p`). Strongest judgment and taste; the only engine with a read-only mode that cannot edit. Holds the Sonnet, Opus, and Fable ladder.
- `references/antigravity.md`: Antigravity CLI (`agy -p`) running Gemini. Cheap parallel capacity for independent, well-specified tasks.

### Choosing an engine

Match the engine to the role and the task, then the account to the engine.

- **builder**: Codex by default. Claude when the change needs taste (user-facing text, API shape, subtle refactor) or when the Codex accounts are busy. Gemini for independent, well-specified pieces when you want more parallel lanes.
- **researcher**: Codex or Gemini for breadth; Claude when sources must be weighed rather than collected.
- **reviewer** and **judge**: Claude in read-only mode, so the reviewer cannot quietly fix what it finds. A Codex reviewer is a fine second opinion for implementation-level review; label it `codex-reviewer`.
- **cleaner**: Claude. This is where the top model earns its price: one pass at the end over work that already passes its checks, rather than a pass per task. Use Fable only on an account whose roster entry lists it.

Spread work across accounts so no single quota carries the run. Never run two workers on the same files at once. Never exceed an account's `parallel` limit from the roster.

### Choosing model and effort

The orchestrator owns this decision per task. Every level is allowed on every engine; pick the cheapest that will finish the task in one attempt, and step up on retry rather than starting high. Each engine reference holds its own ladder, but the shape is the same:

| Level | Task shape |
|---|---|
| lowest | Mechanical: rename, move, fix a typo, write a file from a complete spec, run a command and report |
| middle | Ordinary work with a clear spec: one module, tests for existing code, a document from an outline |
| high | Cross-module change, unclear cause, sources to weigh, approach itself uncertain |
| top | A high attempt failed and the task is genuinely hard, or a wrong approach is expensive to unwind |

Model choice is a separate axis from effort. Raise effort within a model before switching to a stronger model. Reserve the strongest, most expensive model on the roster for `cleaner` and for `reviewer`/`judge` runs where the verdict decides whether the whole run succeeded.

## Setup, once per run

1. Parse `$ARGUMENTS`. Identify the working directory (absolute path, or none) and what the user wants. If the arguments name an engine or a worker, use only that.
2. Split the work into tasks. **Do not over-decompose**: a task one worker can finish in one go stays one task. Spawning many workers for simple work is the most common failure of this pattern.
3. Give every task a **done-criterion a machine can check**: a test command that must exit 0, a file that must exist with given content, a diff that must touch only certain paths. "Looks right" is not a done-criterion. If a task cannot be given one, say so in the final report rather than pretending it passed.
4. Pick a scratch directory: the roster's `scratch_dir`, or the system temp directory. Create `<scratch>/orchestrate-<YYYYMMDD-HHMMSS>/` and write `RUN.md` there with one row per task: `id | task | worker | account | done-criterion | status | attempts`. Status starts at `pending`. Update it after every round; it is the source of truth for the final report and survives a crash. Worker logs and last messages go in the same directory.

## The loop

Repeat until a stop condition fires. Do not return to the user between rounds.

1. **Dispatch** every task whose status is `pending` or `retry` and whose dependencies are met.
   - Assign a worker (`engine-role`), an account from the roster, and a model plus effort.
   - Run independent tasks in parallel; dependent tasks in order. If your harness runs shell commands with a timeout, set it to at least 10 minutes: the default is usually 2 minutes and will kill a worker mid-run. Size each task to finish in about 8 minutes.
   - Write the prompt as if to a contractor who has never seen the project: the goal, the working directory, the files involved, the done-criterion, and what to leave alone.
2. **Verify** each finished task against its done-criterion. Run the check yourself; never accept a worker's claim that it passed. See **Reviewing the work** for when to delegate the reading.
3. **Decide** per task and write it to the ledger:
   - passes: `done`
   - fails, under 3 attempts, cause looks fixable: `retry`, with the failure text added to the prompt and effort raised one step
   - fails twice the same way, or the approach itself looks wrong: **replan**. Rewrite the task, possibly split it or move it to a different engine, reset attempts to 1
   - blocked by something only the user can resolve: `blocked`, and see **When to interrupt the user**
4. **Loop** back to step 1 while any task is `pending` or `retry`.
5. **Clean**, optionally, once everything is `done`: one `cleaner` pass over the whole change set, verified again afterwards with the same done-criteria. Skip it for throwaway work or when the user asked for speed.

## Reviewing the work

The orchestrator always owns the final verdict. Reviewing it yourself is the default; delegating the reading is an option for heavy reviews.

**Review it yourself when** the change is small, or the check is objective and cheap: run the test suite, read a short diff, confirm a file exists with the right contents. Running a test is always worth more than reading an opinion about it.

**Delegate to a `reviewer` or `judge` when** the diff is large or spread across many files, the review needs judgment rather than a pass/fail check, or you need to protect orchestrator context during a long batch. The review prompt must be self-contained: name the files or paste the diff, state what the task was supposed to achieve, and ask for a findings list or a verdict with evidence.

Two rules that always hold:

1. **The reviewer is never the run that did the task.** A freshly spawned worker shares no memory with the one that did the work, so it is a legitimate independent reviewer even on the same engine and account. Never feed a run its own output and call it a review.
2. **A delegated review is evidence, not a verdict.** Run the objective check yourself as well, and decide. Never relay a reviewer's conclusion as if it were confirmed.

## Stopping

Stop when any of these fires. All three categories are live at once.

| Category | Condition |
|---|---|
| **Success** | Every task is `done` |
| **Failure** | A task has failed 3 attempts and one replan · an unrecoverable error (auth failure, missing binary) · a task is `blocked` |
| **Budget** | The roster's `max_rounds`, `max_launches`, or `wall_clock_minutes`, whichever comes first |

**Circuit breakers**, treated as failure stops even with budget left:

- A full round completes with nothing changing status. Looping again will not help.
- The same error text appears twice on the same task. Replan instead of retrying; if the replan produces it a third time, stop.
- A worker edits files outside the working directory, or outside what its task named.

When a stop fires with work incomplete, stop cleanly: finish or kill in-flight workers, update the ledger, and report. Do not keep dispatching around a failure.

## When to interrupt the user

Come back mid-loop only for these. Everything else: decide, write the assumption in the ledger, keep going.

- **An irreversible or outward-facing action the instruction did not clearly authorise**: `git push`, opening a PR, deploying, deleting data, sending a message, spending money, touching production.
- **Credentials or a human-only step** are required.
- **Genuine ambiguity where the readings lead to materially different work** and guessing wrong wastes the whole run. Not "which variable name"; that you decide.
- **All retries and replans are exhausted** and the remaining work depends on the blocked task.

An early return must contain: what is already done, what is blocked, what was tried and why each attempt failed, the specific question or approval needed, and what budget remains. Never come back with only a question.

## Final report

One message at the end, covering:

- **Per task**: worker (`engine-role`), account, model and effort, who verified it, outcome, attempts used.
- **What changed**: files, commits, artefacts produced.
- **Verification**: which done-criteria actually ran and their results. Say plainly if something could not be machine-checked.
- **Assumptions** made in place of asking.
- **Anything skipped or failed**, and why.
- Path to the ledger.

Report outcomes faithfully. A task that failed is reported as failed with its output, not softened.

## Auth and missing binaries

If a worker fails with an auth error, run that engine's login status command (each reference names it) and report it. Do not silently move that task to another account: spreading quota across accounts is the point, and a dead account is something the user needs to know about. An auth failure or a missing CLI is an unrecoverable error: stop the loop and report.
