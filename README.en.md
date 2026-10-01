# coding-agent-from-scratch

[中文](README.md) · **English**

A terminal coding agent written from scratch, with no agent framework. Every line
is hand-written, and every design decision has to come with a reason.

~1,300 lines of core code plus ~800 lines of experiments and evaluation. Seven tools
(`read_file` / `list_files` / `grep` / `edit_file` / `bash` / `delegate` / `run_tests`),
52 tests.

> Modules 0–9 are complete. Every number below comes from raw data committed under
> `results/` and can be re-derived. **Nothing is stated as a conclusion unless it was
> measured** — including three modules whose findings turned out to be negative.

## Running it

```bash
mkdir -p ~/.config/agent-from-scratch
echo "DEEPSEEK_API_KEY=sk-..." > ~/.config/agent-from-scratch/env
chmod 600 ~/.config/agent-from-scratch/env

python3 -m unittest test_tools -v     # no model calls, no cost
python3 agent.py                       # run one task
python3 experiment.py 10               # run a controlled experiment
```

## What was actually measured

### A tool description is part of the prompt, not a comment

Same task, same model (DeepSeek v4-flash), changing only the `grep` tool's
description. 10 runs per arm, arms interleaved with randomised order each round so
that time cannot become a confounding variable.

| | description = "search" | description states the purpose |
|---|---|---|
| used grep on the first step | 4/10 | 10/10 |
| mean input tokens | 7,966 ± 3,132 | 5,918 ± 1,845 |
| total `read_file` calls | 24 | 13 |

**But the mean lies.** Split by step count:

| | 3-step runs | 4-step runs |
|---|---|---|
| description = "search" | 6 runs, mean 5,606 | **4 runs**, mean 11,505 |
| description states purpose | 9 runs, mean 5,344 | **1 run**, 11,078 |

At the same step count the two arms cost almost the same. **The real difference is
the probability of needing a 4th step: 40% vs 10%.** A 4th step costs roughly
6,100 extra tokens; `(0.4 − 0.1) × 6,100 ≈ 1,830` predicts the observed difference
of 2,048.

Conclusion: a good tool description **does not make each step cheaper — it makes the
expensive failure path rarer.** The effect lives in the tail of the distribution, not
at the centre. The longer description itself costs ~90 tokens per call, far less than
what it saves.

### I picked the wrong metric three times

| Attempt | Metric | Outcome |
|---|---|---|
| 1 | total tokens, n=3 | within-group SD 468 vs between-group difference 212 — noise swamped the signal |
| 2 | binary "did it use grep first" | measured the wrong thing; two independent runs gave 8/10 and 4/10 |
| 3 | re-analysed saved raw trajectories | found the real driver: number of `read_file` calls |

**The right metric was discovered in the raw data, not designed up front.** That is
why raw trajectories are committed — the next question you want to ask is never the
one you anticipated when designing the experiment.

### SEARCH/REPLACE: four matching strategies, only two ever fire

Models paraphrase code when quoting it — they drop indentation, swallow trailing
whitespace, truncate long lines. Exact character matching therefore fails often, and
every failure costs another round trip. Hence a chain of strategies from strict to
loose, where **each level independently re-validates uniqueness**, because loosening
the match is exactly what creates ambiguity.

| Strategy | What it does | Can it silently edit the wrong place? |
|---|---|---|
| `exact` | character-for-character equality | no |
| `normalized` | equality after stripping trailing whitespace (character-level) | no |
| `indent` | equality after removing common indentation | no |
| `fuzzy` | similarity ≥ 0.85 and clearly ahead of the runner-up | **yes** |

Measured over 42 real edits (DeepSeek v4-flash, target file restored before every run):

| File state | Edits | `exact` | `normalized` | `indent` | `fuzzy` |
|---|---|---|---|---|---|
| clean file | 12 | **100%** | 0 | 0 | 0 |
| trailing whitespace on method bodies only | 13 | **100%** | 0 | 0 | 0 |
| trailing whitespace throughout | 30 | 70% | **27%** | 0 | 0 |

Three conclusions:

**The normalisation layer's value depends entirely on how dirty the file is.** It never
fires on a clean file; when trailing whitespace is everywhere, about a quarter of all
edits are rescued by it. The question is not "is it useful" but "under what conditions".

**The model's handling of invisible whitespace is all-or-nothing.** Within one edit's
`old_str`, trailing spaces are either all preserved or all dropped — never mixed. It
switches between "copying character by character" and "regenerating normalised code",
rather than randomly losing characters. That means the normalisation layer only has to
handle one case.

**`indent` and `fuzzy` never fired in 42 edits, but they should not be removed
together.** The first three are *normalisations* (reduce both sides to one form, then
require equality) and cannot match genuinely different code. `fuzzy` is a *guess*, and
is the only path that can silently edit the wrong place. So "never fired" means
different things: `indent` is zero-risk and costs a constant ten lines, worth keeping
as insurance; `fuzzy` is the largest chunk of code *and* carries miswrite risk, so it
is the one that deserves scrutiny. **Fault tolerance has to be evaluated by the cost of
its failure mode, not uniformly.**

### Where to set the threshold: the two error types have asymmetric costs

Fuzzy matching has two gates — an absolute threshold of 0.85, and "the best match must
beat the runner-up by 0.05". The second matters more: with two similar functions in a
file, 0.93 and 0.91 both clear the absolute threshold while you still have no idea
which one the model meant.

| Error type | Consequence | Cost |
|---|---|---|
| false reject (a valid match refused) | the model retries with more context | one round trip, **recoverable** |
| false accept (wrong place edited) | the file is silently corrupted, "edited" is returned, the model believes it and moves on | **unrecoverable, nobody notices** |

The costs differ by orders of magnitude, so the threshold leans clearly conservative.
In testing it did falsely reject one edit that would have been correct (0.96 vs 0.92,
a gap of 0.04 < 0.05) — that is the price paid deliberately.

### Repo map: using graph ranking to decide what goes into context

The agent once read every file in the project to answer a single question, burning
48,411 tokens. If six files cost that much, a real repository is hopeless. So:
**given a task, which small part of the code should go into context?**

Four steps: tree-sitter parses the AST to extract "who defines what, who references
what" → files are connected into a directed graph by those references → personalised
PageRank ranks them → the ranking is rendered into a map injected into context.

**① Import constraints removed the false edges.** With symbol-name matching alone,
only 6 of this repo's 19 edges were real (`set().add()` colliding with `Cart.add`,
`unittest.main()` colliding with the experiment scripts' `main()`, `cart.py` and
`cart_dirty.py` linked to each other although neither knows the other exists). In
Python you cannot use what you have not imported, so that hard constraint was added:

| | Edges | Real edges | Precision |
|---|---|---|---|
| symbol-name matching only | 19 | 6 | **32%** |
| plus import constraint | 6 | 6 | **100%** |

No real edge was lost. The cost is losing multi-language generality — import semantics
differ per language, which is exactly why aider, supporting dozens of languages, skips
this and dilutes the noise with weights instead. **This is a precision-vs-generality
trade-off, not one approach being right.**

**② Un-personalised PageRank is useless for a repo map.** Dependency arrows point
downward toward primitives, so on mini-swe-agent (112 files) the top results are
`exceptions.py` and `serialize.py` — utilities everyone imports — while the core
`agents/default.py` ranks 9th. Reverse the edges and the top becomes test files and
entry points. **"Most depended upon" and "most worth reading" are different things —
personalisation is not an optimisation, it is the only reason the method works at all.**

**③ The bottleneck is query understanding, not the ranking algorithm.** The first seed
extractor only did exact identifier matching, so "how is the docker image name
assembled when swebench runs in batch" extracted **zero** seeds — because the symbol is
called `get_swebench_docker_image_name` and the file is `swebench.py`, and humans do
not talk that way. Worse, with an empty seed PageRank silently falls back to a uniform
distribution and **emits a plausible-looking ranking that has nothing to do with the
task** (fail-open, again).

The second version summed over "how many symbols matched", which turned into sorting by
file size: asked about `LitellmModel`, the top hit was `portkey_response_model.py` —
it has a dozen symbols containing `model`, each worth a point.

The third version fixed it with IDF: **each task word contributes at most once per
file, weighted by that word's rarity.**

| Task word | Appears in | IDF |
|---|---|---|
| `litellmmodel` | 1 file | **4.04** |
| `api` | 21 files | 1.81 |

`litellm_model.py` went from being outranked to leading the runner-up by 5.5×.
**The ceiling of a retrieval system is usually set by query understanding, not by the
ranking algorithm.**

**④ Effect: every metric improved on a 112-file repository.** 3 read-only Q&A tasks ×
3 rounds, arms interleaved:

| | map off | map on | |
|---|---|---|---|
| steps | 6.8 | 5.4 | −21% |
| total input tokens | 64,397 | 43,394 | **−33%** |
| uncached input (full price) | 13,709 | 10,327 | **−25%** |
| output tokens | 1,517 | 1,284 | −15% |
| `read_file` calls | 4.1 | 3.6 | −12% |

The map itself is ~600 tokens and is resent every step — about 3,200 tokens of fixed
overhead across 5.4 steps — **yet it returned a net saving of 21,000**. The saving does
not come mainly from "reading fewer files" but from **fewer steps**: history is
cumulative, step 7 resends everything from steps 1–6, so cutting the last 1.4 steps
saves the most.

**Same structure as the tool-description finding: the effect is in the tail of the
distribution, not in the average per-step cost.**

**Retroactive calibration**: module 5 used an A/A test (two arms with identical
configuration) to measure a noise floor of about **26%** on input tokens for this
experimental design. The −33% above is only slightly above that floor and strictly
needs more repetitions to stand. I had no noise floor to compare against when I wrote
this section, and stated the conclusion too firmly.

At n=1 the same experiment showed input tokens **+6%**, making the map look like a
loss; at n=3 it became −33%. **Same experiment, three times the sample size, opposite
sign.**

Known limits: one repository, three read-only Q&A tasks, no editing tasks; Python only;
the task-side tokeniser does not split camelCase, so a user writing "default agent"
instead of `DefaultAgent` degrades to generic-word matching.

### A/A testing: you cannot judge an effect until you know the noise

A mis-written experiment switch (`RANGE_READS` was defined but never read inside the
function) made both arms run **identical configurations**. It was a bug, and it became
the most valuable measurement in the project — an **A/A test**: two identical arms, so
every difference between them is noise.

9 runs × 2 arms, same code, same tasks:

| Metric | Arm 1 | Arm 2 | Difference caused by noise alone |
|---|---|---|---|
| input tokens | 32,731 | 44,197 | **26%** |
| steps | 6.7 | 8.1 | **17%** |
| context characters | 24,625 | 29,000 | **15%** |
| uncached input | 5,866 | 6,835 | **14%** |

**From then on, any n=9 comparison showing less than a 26% difference in input tokens
is indistinguishable from noise.**

That number was then turned back on every earlier conclusion (see the retroactive
calibration above). **An A/A test should be the first step of a controlled experiment,
not the tenth.**

Another observation: **the noise floor on context characters (15%) is clearly lower
than on total tokens (26%)**, because it is a deterministic measurement of the final
message list with one fewer layer of stochastic model decisions in between.
**Metrics closer to the mechanism are less noisy.**

### The cheapest compression is not producing the data

Measure where the tokens go before deciding how to save them. Bucketing the message
list by source (tool results bucketed by **which tool produced them** — otherwise
"tools account for 82%" tells you nothing):

| Source | Share |
|---|---|
| `tool result / read_file` | **82%** |
| `tool result / grep` | 8% |
| task + repo map | 4% |
| everything else | 6% |

Three `read_file` calls produced 44,436 characters, because each read **a whole file**.
Two possible responses:

| Approach | Effect on prompt caching |
|---|---|
| compress history (summarise / drop old messages) | **rewrites the prefix → cache invalidated** |
| do not produce it: give `read_file` a line range | **never touches history → cache fully preserved** |

The second was chosen. `grep` already returns line numbers, so the information chain
was complete — the model simply had not been given the capability.

Controlled experiment (3 tasks × 3 rounds, arms interleaved), each effect compared
against the noise floor above:

| Metric | off | on | Effect | Floor | Verdict |
|---|---|---|---|---|---|
| context characters | 36,438 | 25,013 | **−31%** | 15% | **holds** |
| uncached input | 7,986 | 5,840 | **−27%** | 14% | **holds** |
| total input tokens | 38,364 | 26,818 | −30% | 26% | marginal |
| steps | 6.0 | 5.9 | −2% | 17% | **no effect** |
| correct | 7/9 | 8/9 | +1 | — | indistinguishable |

**The noise floor killed one of my own claims.** I had stated the step-count effect
twice, in opposite directions ("it costs 3 extra steps", then "it actually saves 1.4
steps") — both times reading noise off **unpaired single runs**. The truth is that
range reads do not change the step count at all.

It also confirmed that correctness did not regress: the tokens saved were not paid for
with answer quality.

(Correctness is judged by keyword matching — crude, but objective and repeatable. That
metric itself has not been validated: whether the "wrong" answers were genuinely wrong
or the check was too strict would require reading the full answers in the raw data.)

### A negative result: once tool output is capped, history compression is unnecessary

Module 5.2 solved "do not put junk into context". The remaining question is whether to
compress what is already there. The textbook answers are a sliding window or
summarisation. **But first confirm the bottleneck exists.**

A survey task spanning 7 files and 928 lines was built, expected to produce a history
of 15+ steps. Measured: **4 steps, 10 tool calls.**

```
step  input    delta
1     1,392    +1,392
2     1,701    +309
3     6,381    +4,680     ← one round read several files at once
4     12,630   +6,249     ← another batch
```

**The model calls tools in parallel** — several `read_file` calls in one round rather
than read-one-then-ask. Context grew to 54,611 characters while history was resent only
4 times.

> **Parallel tool calls decouple "step count" from "context size."** The O(N²) growth I
> expected never happened.

The arithmetic of compression: the `read_file` content is about 12,000 tokens, entering
mostly at steps 3 and 4, so it is **resent at most once** — eliding old results saves at
most ~4,500 tokens. Meanwhile step 4 had **6,528 tokens served from cache**, and
rewriting history breaks the prefix, turning all of those into full price.

**Save ~4,500, lose ~6,500 from cheap to expensive. The sign is negative, not merely
small.**

What actually produces long histories is a workload whose **steps depend on each other
and cannot be parallelised**: edit code → run tests → read the error → edit again. At
that point the agent had only read/search/edit tools and **no ability to execute
commands**, so such a loop could not be constructed.

**So 5.3 could not be measured then** — the dependency ran the other way, and command
execution (module 6) had to come first. That ordering was decided by data, not by plan.

**Re-measured after module 6.** A fixture with 5 independent bugs forces "run tests →
see failure → locate → fix → run again", each step depending on the last. Measured:
**13 steps, 13 tool calls**, all bugs fixed with 7/7 independently verified — a genuinely
serial trajectory. But:

```
step   input    cached   uncached   delta
 1    1,055      640        415    +1,055
 4    2,724    2,560        164      +194
 8    3,215    3,072        143      +141
13    4,226    3,840        386      +399
```

**Increments stay at +100–300, 13 steps end with only 11,013 characters of context,
and the cache hit rate is 89%.**

The two workloads avoid needing compression for **opposite reasons**:

| | survey task | serial bug-fixing |
|---|---|---|
| steps | 4 | 13 |
| final context | 54,611 chars | 11,013 chars |
| reason | large context but **few resends** | many steps but **little to resend** |

Compression only pays when there are many steps **and** each step adds a lot. The
per-tool output caps (`MAX_READ_LINES=400`, `MAX_BASH_OUTPUT=8000`, `MAX_HITS=200`)
already falsify the second condition.

Boundary estimate: in the worst case, every step filling 8,000 characters of bash output
(≈2,200 tokens) would need **about 58 steps** to fill a 128k window; at the measured rate
(167 tokens/step) it would take **over 700**.

> **"Not producing it" made "compressing it" unnecessary** — measured on two different
> workloads, not inferred.

**Unexpected finding: the agent's own tool-call arguments account for 29% of context**
(`edit_file`'s old_str + new_str), comparable to the file contents it read in (24%).
That part **cannot be capped**, because it is model output rather than tool output. It
retroactively validates the module 3 choice: had whole-file rewriting been chosen, this
bucket would have become overwhelmingly dominant, since every edit would emit the entire
file as `new_str`. **That decision was made by reasoning at the time; now it has data.**

### Sandbox: build the constraint before enabling the capability

Module 6 gives the agent the ability to run arbitrary commands. The motivation is
concrete: **before that, in order to run tests, it executed `pip install --user pytest`
and installed a package into my user environment.** That one was harmless, but the same
path can write any file and reach any network.

**An approval prompt does not stop this.** Nobody can see what is buried in a long
command at a glance, and after pressing "yes" ten times, the eleventh is not read.
Approval answers "I permit you to do this"; a sandbox answers **"even if I permit it,
you cannot get out"** — different layers of defence.

So this module **built and escape-tested the sandbox before connecting the `bash` tool**.
The dangerous capability was never live unguarded.

**Policy design (macOS Seatbelt / SBPL)**: `(allow default)` followed by targeted denies,
rather than the reverse. The reverse is safer but blocks the dozens of syscalls Python
needs to start. The price is that **"whatever you forgot to deny is allowed" — so actual
behaviour has to be verified by escape tests, not by reading the policy text.**

The workspace path is **interpolated into the policy text**, so `_sbpl_string`
**refuses rather than escapes** paths containing quotes or backslashes: one quote would
close the string early and inject `(allow file-write*)`, disabling the sandbox outright.
This is the same shape of problem as SQL injection, and paths with quotes are vanishingly
rare — **refusing is less error-prone than writing escaping rules.**

Measured (9 escape tests): writable inside the workspace, `Operation not permitted`
outside it, readable outside it (required, or Python cannot load its libraries), network
blocked, Python working normally. `pip install --user` is stopped by **two independent
layers**: the network is cut so nothing downloads, and even if it did, `~/Library/Python`
is outside the workspace and unwritable.

### Two "fake tests": asserting that nothing bad happened is not enough

**First time.** `test_network_is_blocked` was written as "if it cannot connect, it
passes", and all 9 tests were green. A control run with the sandbox removed:

```
sandboxed     BLOCKED  (0.05s)
unsandboxed   BLOCKED  (5.05s)   ← passes without the sandbox too
```

The probe target `1.1.1.1:443` was simply unreachable on that network, so a 5-second
timeout produced the same BLOCKED result. **That test was green in any environment,
which makes it equivalent to no test at all.**

The fix is to assert **evidence of refusal** instead of **absence of success**: the
sandbox denies with `PermissionError` (EPERM), while an unreachable network gives
`timeout` or `gaierror`. EPERM can only come from the sandbox. Plus a **test of the
test** — the same probe outside the sandbox must never return EPERM, or the previous
test has lost its meaning.

> Absence of success has many causes (no network, firewall, broken DNS). EPERM has one.

**Second time, one turn later.** The `bash` tool was completely broken (a missing
`import sandbox`), and two freshly written tests were still green: "no file was created
outside the sandbox" (the command never ran) and "an interactive command did not hang"
(it errored out instantly). **The principle had just been written down, and the very
next tests violated it again.**

General fix: **every "nothing bad happened" assertion needs a companion assertion that
the operation actually ran** — the `[exit code` marker only appears once
`subprocess.run` has genuinely returned.

### fail-closed: the same principle, four times

| Situation | What fail-open would mean | What was done |
|---|---|---|
| `WORKSPACE` config degrading to an empty string | the boundary check becomes decorative | assertion at startup |
| 429 indistinguishable between rate limit and empty balance | six wasted back-offs | classify by message; empty balance fails immediately |
| sandbox unavailable | commands run unconstrained | `REQUIRE_SANDBOX` refuses to execute |
| no terminal available | **equivalent to auto-approving every write** | refuse |

The last one surfaced when the approval gate was moved from `input()` to `/dev/tty` —
stdin is frequently redirected (heredocs, pipes, CI), and `input()` then hits EOF
immediately. **"If nobody can be asked, assume consent" fails exactly when running
unattended, which is precisely when it matters most.**

### A serial workload finally appears

With `bash` connected, the first real "edit code → add a test → run tests to verify"
loop ran. The agent independently added price validation plus three tests, two of which
were not asked for: verifying that **a rejection leaves no partial state**, and pinning
down the **boundary (0 is legal, only negatives are refused)**. Independently re-run:
9/9 passing.

Such tasks have **interdependent steps that cannot be parallelised** — exactly the
workload module 5.3 was missing. History compression could now be re-measured on it.

### Evaluation harness: making "does the effect exceed the noise" the default output

Previously each experiment had its own task set, metrics and file format, so conclusions
could not be compared; and **only one accidental A/A had ever measured the noise floor**,
meaning the module 2 and module 4 conclusions were drawn without knowing how large the
noise was.

`evaluate.py` consolidates this, and its central design is: **the baseline arm is
automatically duplicated as an A/A control during every batch run**, with the report
printing each arm's effect next to the noise floor and marking which conclusions can be
believed.

> The noise floor used to be something we stumbled into. Now it is a default product.

Three supporting decisions:

- **Configuration is restored immediately after use.** The agent's switches are
  module-level globals (a smell introduced back in module 2); without restoration, arm
  A's configuration leaks into arm B — contamination that raises no error and silently
  corrupts the results.
- **Repair tasks are verified by an independent command**, checking the real test exit
  code rather than trusting the agent's claim that it passed.
- **Fixtures are restored before every run**, so each arm starts from identical state.

**The first run corrected an error of my own.** The report originally used
`|arm − baseline| / baseline`, where baseline was just one of the two A/A arms. In that
run the two identical arms differed by 41% on prompt tokens — **whichever one is chosen
as the baseline moves the measured effect between 22% and 107%.** After pooling both as
the baseline estimate and using their difference as the noise floor:

| Metric | Baseline | Floor | Effect of whole-file reads | Verdict |
|---|---|---|---|---|
| uncached input | 5,618 | 22% | **60%** | **✓** |
| context characters | 24,739 | 24% | **56%** | **✓** |
| total input tokens | 57,202 | 52% | 54% | ✗ |
| steps | 11 | 22% | 1% | ✗ |
| correctness | 67% | 22% | 11% | ✗ |

The two survivors are exactly the two module 5.2 identified, **both with more than 2×
margin over the floor** — an independent replication.

**The correctness row is new.** The report originally computed no noise floor for
correctness, so "whole-file reads answered 7/9 versus 5/9 for range reads" looked like a
finding; with a floor it is clear that **two identical configurations already differ by
22%** — that was noise.

### The noise floor is itself noisy

| Measurement | prompt noise floor at the same n=9 |
|---|---|
| module 5.2 | **15%** |
| module 7 | **41%** |

The floor is estimated from the difference between two sample means, so **it is itself a
random quantity**. Verdicts near the boundary are therefore unstable. The rigorous answer
is to run several A/A groups and take the distribution, which is too expensive. The
practical compromise: **believe only effects far above the floor and stay sceptical of
marginal ones** — the two ✓ above exceed 2×, while `prompt` at 54% vs a 52% floor is not
accepted.

### Sub-agents: isolation works, but the total bill is worse

A `delegate` tool spawns a **read-only sub-agent** with its own message list; **only its
conclusion returns to the main context**, and everything it read is discarded.

**The easiest way to cheat was closed first.** If only the main loop's tokens are
counted, isolation inevitably looks like a win — exploration cost is moved into a
throwaway context and disappears from the main ledger. **But the money was still spent.**
So the code accounts for sub-agent usage, and `total_prompt` is the only honest cost
metric in the report.

**The model never uses it voluntarily.** Across four runs and three rounds of
inducement, `delegate` was called 0 times:

| Attempt | Result |
|---|---|
| initial description (mechanism only: "the sub-agent has its own context") | 0 |
| description rewritten to state the benefit + explicit system-prompt instruction | 0, but it **switched to range reads**, tokens −14% |
| task restructured into three independent sub-questions | 0; it **used parallel tool calls instead**, 4 steps, 21,670 tokens, all correct |
| main agent's read limit cut to 40 lines | 0; it chunked through instead, and **at step 12 bypassed the limit with `cat` via `bash`** |

Two findings:

**① Cost framing only changes behaviour that is executable within a single step.** It
adopted range reads (a within-step choice) but not delegation — **delegation requires
first decomposing the task into a self-contained sub-question, which is a planning act**,
whereas ReAct decides only "what to do next" at each step. At that granularity there is
nothing to delegate. Delegation presupposes decomposition.

**② A restriction on one tool means nothing while another tool offers the same
capability.** I lowered `read_file`'s limit; the agent found `cat` through `bash` on its
own. This is the other face of the module 6 lesson about pushing constraints down to the
OS layer: **capability constraints belong at the capability level, not the tool level.**

With `bash` also removed from the main agent, delegation finally occurred. The A/B then
used two arms **restricted identically** (40-line limit, no bash), differing only in
whether `delegate` existed. 3 breadth tasks × 3 rounds:

| Metric | Baseline (delegation available) | No delegation | Effect | Floor | Verdict |
|---|---|---|---|---|---|
| **total input tokens** | 184,130 | 107,466 | **+42%** | 19% | **✓ delegation costs more** |
| main context characters | 51,460 | 65,759 | −28% | 10% | ✓ isolation works |
| main-loop tokens | 85,631 | 107,466 | −25% | 4% | ✓ |
| **completion rate** | 56% | 56% | 0% | 22% | **✗ no difference** |
| **correctness** | 56% | 56% | 0% | 22% | **✗ no difference** |

**Net: 42% more tokens buys 22% less main context, with no change in completion or
correctness — a loss at this scale.**

**A single observation misled me again.** The first forced-delegation run was the only
one of three configurations to reach `finished`, and I wrote that "it turned failure into
success". After 9 repetitions: 67% vs 56%, while **the two identical A/A arms were 67%
vs 44%** with a 22% floor. That "only one that finished" was luck. **This is the fourth
time in this project that an unrepeated single observation pointed the wrong way.**

**The same data also exposed two measurement flaws of my own:**

- The `uncached` column counted only the main loop, **ignoring the sub-agent's uncached
  input entirely**, making delegation look 27% cheaper on full-price input.
  `total_prompt` caught that trap; this column did not. It has been changed to full
  scope, but **the column in this table is still main-loop-only and must not be used for
  cost judgement** — the flaw is recorded rather than quietly deleted.
- `finished` is a rate but was formatted as an integer, so 0.55 displayed as `1` and a
  reader would assume a 100% completion rate.

**Same structure as module 5.3**: the grep guidance of module 2, the range reads of
module 5.2 and the output caps of module 6 had already driven exploration costs low
enough that **the problem sub-agents solve had been consumed by earlier work.** Two
textbook agent patterns — history compression and sub-agent isolation — were both made
unnecessary in this architecture by what came before them.

### Reflexion could not be measured, and why that is itself a result

Measuring Reflexion (self-critique after failure, then retry) requires the **baseline to
fail often enough** — at a 100% pass rate no improvement can show. So fixtures were
built, targeting a 40–60% baseline pass rate.

**Three rounds failed to push it down:**

| Fixture | Design | Baseline pass rate |
|---|---|---|
| `shop` | 5 obvious bugs (minus written for plus, wrong constant) | **6/6** |
| `shop-hard` | 5 subtle bugs (mutable default argument, `is` on strings, coupon/tax ordering, unformatted float, missing lower clamp) | **6/6** |
| `shop-hidden` | same, but the **test source is invisible to the agent**; it can only run them and infer the spec from assertion messages | **6/6**, in 4 steps |

While building `shop-hard` I tripped first: **two of my "bugs" were not bugs at all.**
"Tax before discount" is commutative under multiplication (`x·1.08·0.9 ≡ x·0.9·1.08`);
and with `is` on strings, `"apple" + " " + "pie"` is constant-folded at compile time into
an interned literal, so `is` actually succeeds. **The third time this project was bitten
by "the task set must be validated first"** — they were replaced by a fixed-amount coupon
(where ordering matters) and a runtime `" ".join([...])`.

`shop-hidden` applied the module 9 lesson: **making `read_file` refuse is not enough, a
single `cat` through `bash` bypasses it.** So the tests live outside the workspace (where
the boundary check refuses `read_file` naturally), the sandbox gets a targeted
`(deny file-read* ...)` rule to block `cat`, and the agent gets a `run_tests` tool that
runs the suite outside the sandbox and **filters the source lines out of the traceback**,
returning only test names, results and assertion messages.

Conclusion:

> **Reflexion could not be measured in this project — not because it does not work, but
> because no task set could be built where the baseline fails often enough.** Reaching
> that difficulty requires real issues in real repositories, and **building such a task
> set costs more than building the agent** — which is exactly why SWE-bench exists as a
> research project in its own right.

It did establish one mandatory design for any Reflexion experiment: **the control arm
must be "retry without critique"**, not "one attempt only". Otherwise what is measured is
the effect of granting a second budget — a trap that any experiment inducing failure by
lowering a step limit will fall into.

### A fifth kind of fail-closed: if you cannot measure it, do not report a number

The hidden-test baseline reported **0/3 correct**, which looked like "hidden tests defeated
it". In fact the verification command used a relative path and never ran at all
(`ImportError: Start directory is not importable`) — **the agent had fixed everything in
four steps**, independently re-verified at 6/6.

> **A broken verifier and a failing agent look identical on the report.**

The only tell was that "4.3 steps" was implausibly low. The fix is to make the verifier
**raise** when it fails to run, rather than quietly returning "incorrect":

```python
    if "Ran " not in out:
        raise RuntimeError(f"the verification command never ran; this result is not trustworthy:\n{out[-400:]}")
```

This is the fifth appearance of fail-closed in the project, and a new category — the
first four were "refuse when authorisation cannot be obtained", this one is **"do not
report a number you could not measure"**.

## Problems hit and fixed

| Problem | Root cause |
|---|---|
| 401 Unauthorized | `"Bearer" + KEY` missing a space; the space in an HTTP auth header is part of the grammar |
| the agent read `.env` and sent the key to the model | `read_file` had no path constraint. Fix: move the secret out of the working directory (shrink the blast radius) plus add a boundary check |
| `/etc/passwd` bypassed the boundary check | `os.path.dirname(".")` returns an empty string, degrading the check into "is this an absolute path" — **fail-open**. Fix: `realpath` plus a startup assertion |
| the model's grep calls kept failing while the agent reported nothing | the schema was written, the registry was not. Fault tolerance silenced the bug; fix: assert the two agree at startup, and print `⚠` on tool errors |
| the model used a partial line as an insertion anchor and all four strategies failed | strategy 1 is character-level while strategies 2–4 compared whole lines. That assumption was never written into any interface, so the model violated it. Fix: make the normalisation layer character-level too (normalise + index mapping), solving trailing whitespace and partial lines at once |
| an end-to-end experiment passed 10/10 while a `NameError` sat in the code | a refactor deleted strategy 2's intermediate variable while strategy 3 still referenced it. That branch **never executed**, so the integration run looked perfectly healthy — caught by a unit test. **End-to-end only covers the paths actually taken** |
| 12 experiment runs before noticing the described bug did not exist | the agent was asked to fix "percent == 100 is wrongly rejected" when the code was already correct. It correctly refused to fabricate a change, but one run burned 48k tokens searching. **A task set must be validated before use** — the contract is now pinned by `test_cart.py` |

## Design conventions

- **Tools always return a string and never raise.** Raising removes the model's chance to
  correct itself.
- **Every tool caps its own output** (`grep` truncates at 200 hits). One runaway search
  can fill the context window.
- **Path checks use `realpath`, not `abspath`**, or a single symlink inside the workspace
  walks straight out.
- **Security-relevant configuration is asserted at startup.** Silently permitting is more
  dangerous than having no check.
- **Anything that must be kept consistent in two places is either merged or checked
  automatically.** Memory does not work.
- **Fault tolerance is classified by the cost of its failure.** Normalisation (reduce both
  sides to one form, then require equality) can be added freely; guessing (accepting "not
  equal but similar enough") has to earn that risk first.

## Roadmap

- [x] Module 0 — hand-written curl calls, seeing that an agent is just JSON round trips
- [x] Module 1 — minimal loop plus the tool-calling protocol
- [x] Module 2 — tool layer, error contract, path safety, first controlled experiment
- [x] Module 3 — code editing and the matching strategy chain
- [x] Module 4 — repo map: tree-sitter plus graph ranking
- [x] Module 5 — context budget and compression (including the 5.3 re-measurement on a serial workload)
- [x] Module 6 — execution sandbox (macOS Seatbelt) and command execution
- [x] Module 7 — unified evaluation harness with an automatic A/A baseline
- [x] Module 8 — Reflexion: three fixture generations all failed to push the baseline below 100%; not measurable (see above)
- [x] Module 9 — sub-agents: isolation works but the total bill is negative; the model never uses it voluntarily
