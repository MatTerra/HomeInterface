---
name: delegate
description: Rules for handing work to subagents in this repo — always pin the model explicitly, never let Opus run mechanical work. Use when spawning an Agent, when the user says to delegate/use a subagent, or when deciding whether a task should be delegated at all.
user-invocable: true
---

# Delegating work in this repo

The main session is for analysis, spec-shaping, validation and review. Implementation that is already pinned down goes to a subagent on the smallest model that can do it.

## The rule

**Never spawn an agent without an explicit `model`.** Omitting it inherits the parent model — which is usually Opus, and that is exactly the waste this skill exists to prevent.

Pick by how defined the spec is at the moment of spawning:

| Model | When | Examples |
|---|---|---|
| `haiku` | Spec fully defined, or read-only exploration | Exact files and exact edits named; mechanical propagation across layers; renames; adding a field through model → repo → router → frontend type; "find where X is built", "list every callsite of Y" |
| `sonnet` | Spec still somewhat open | Design decisions remain; unclear where the code should live; needs judgement about an existing pattern |
| `opus` | Effectively never | Only if sonnet has already tried and failed on the same spec, and you can say what it got wrong |

If you are reaching for `opus`, the real problem is usually that the spec is not finished. Finish the spec in the main session instead — that work is already paid for.

## What does not get delegated

- The analysis that produces the spec. Delegating it just re-derives context this session already holds.
- Validation and review. Run tests, lint, typecheck and the actual verification here, where the results can be judged against the original intent.
- Anything the user asked *you* to look at. "Review my work in progress" means review it, not fan it out.

A task being large, multi-part, or described as "thorough" is not a reason to delegate. Cost is per-spawn and each spawn starts cold.

## How to spawn

```
Agent(
  subagent_type: "Explore",         # read-only fan-out searches
  model: "haiku",                   # ALWAYS set this
  description: "Find vector metadata fields",
  prompt: "<concrete spec — exact files, exact expected output>"
)
```

Notes specific to this repo:

- `Explore` for read-only searches; `general-purpose` or `claude` when the agent must edit files.
- `subagent_type: "fork"` ignores `model` — forks always inherit the parent. Do not use a fork to run cheap work.
- Give the agent the paths you already know. It cannot see this conversation, and re-discovery on a cold start is where delegation stops paying for itself.
- Ask for file:line evidence back, so the result can be checked without re-reading everything.

## Sanity check before spawning

1. Can I name the files and the expected output? → `haiku`.
2. If not, can I name the goal and the constraints? → `sonnet`.
3. If not, the spec isn't ready. Keep it here.
