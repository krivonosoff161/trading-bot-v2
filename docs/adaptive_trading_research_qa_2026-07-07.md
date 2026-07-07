# Adaptive trading research Q&A - 2026-07-07

This file preserves the detailed question/answer decisions from the July 7
architecture discussion. It is intentionally more verbose than the handoff file
because the nuance matters for the next implementation pass.

## 1. Farm goal

Question: Should the farm optimize for maximum return, stable repeatability, or
both modes?

Answer: Both modes are needed separately:

- quality mode: fewer signals, deeper checks.
- flow mode: more signals, more learning material.

Additional operator note: do not overload the PC/GPU. The farm should work
gradually, keep some GPU memory free, and prioritize current/live opportunities
over background research. Heavy background sweeps should not block the working
paper loop.

Question: Should the farm search for the best current signal or the best system
for a pair?

Answer: Both over time. The immediate output should be a usable current signal,
but the long-term point is to let the farm discover better patterns, filters,
and new indicator-like structures from repeated cycles.

Question: If the farm finds several variants for one market situation, what
should go to Telegram?

Answer: One best variant, or at most two. Do not flood users with many variants
and create confusion. The main criterion is profit potential plus quality of
the check.

## 2. Time horizon

Question: Should the system have separate signal classes such as scalping,
intraday, swing?

Answer: The system already writes hold time in cards, sometimes from one hour
to six hours, but accuracy is questionable because TP/SL and exits are
currently rough. There is no reliable horizon model yet.

Question: Should the user-facing card show the type/horizon?

Answer: It already writes something similar, but often too uniformly. This
should be checked against real cards before changing.

Question: If a signal is on `1h` but useful movement happens faster on `15m`,
who should detect this?

Answer: The farm calculates the signal on the selected timeframe, for example
`1h`, and should understand it as a longer trade. But if the system can capture
several faster moves, or sees reversal risk, it should consider faster exits or
moving stop logic. Longer trades may need trailing-stop recommendations.

## 3. TP, SL, entry, and exit

Question: What is the biggest exit problem?

Answer: The system can exit outside the real movement. It may close/treat the
trade as done while the actual move is only beginning. This causes missed
profit and weak economics.

Question: Should every signal have several exit scenarios?

Answer: Yes. The farm should account for quick exit, base exit, extended hold,
and trailing-stop variants.

Question: Should partial exits be shown to users now?

Answer: Not necessarily in Telegram. But trailing stop and partial/ladder logic
can be modelled internally. This may become a separate contour because it needs
validator support and comparable data.

Question: Should "price went into profit but final result was weak/negative" be
a separate error class?

Answer: Yes. `missed_profit_capture` or a similar label should be first-class.

Additional operator note: the current TP/SL often appears too close to entry,
almost near commission/noise level. This suggests the farm may be using static
or too narrow parameters instead of adaptive market-specific calculations.

## 4. Validator

Question: Should validator be pass/fail or graded?

Answer: It should have grades/statuses:

```text
rejected
watch_only
experimental
paper_allowed
pfr_validated
```

Question: What if validator rejects a signal but paper later shows it had value?

Answer: This should become validator learning data. The validator may be too
strict or too inflexible. The case should not be discarded; it should be stored
as a possible false reject.

Question: Should validator consider current market regime?

Answer: Ideally yes, but full live context can overload the machine. Need a
careful design for what current context is loaded and how often.

Question: Should validator account for commission, spread/slippage, leverage?

Answer: Yes. This is strict validation, not just plus/minus direction.

Question: If a signal is profitable only before commission, what then?

Answer: It should be sent for recalculation/review because the system may be
taking the end of an impulse or using a wrong TP/entry model.

Additional operator note: the system should not become only scalping. It needs
several horizons, from fast/minute ideas to intraday and longer ideas.

## 5. LLM in the farm

Question: Should LLM propose ranges or select from already calculated results?

Answer: LLM should propose what to test and expand the search. Static presets
are not enough.

Question: Can LLM read candles/graph data?

Answer: The farm should calculate from all useful data: candles, technical
analysis, stored data, and possibly visual/graph-like representations if
feasible. Otherwise calculations stay too poor.

Question: Can LLM ask project code to calculate another variant?

Answer: Yes. It should work through code, not invent results itself. It has no
trade authority. It uses project code, data, and controlled calculation paths
to avoid hallucinated outputs.

Question: Can LLM create new pattern hypotheses?

Answer: Yes, but those hypotheses must pass the full cycle. If a new idea
produces profit and outcome analysis confirms value, it should become a marked
research case and may later become part of the calculation system.

Question: Is an orchestrator needed?

Answer: Yes. A coordinator should decide which role/model is needed. Calling
all models all the time is not justified.

Additional operator note: for calculator/farm work, local models are preferred.
Cloud models can support other roles, but the calculator-like mini-swarm should
be local where possible. Local models should be calibrated on real project
work, not just prompted generically.

## 6. Memory and learning

Question: Is "learning" just rules/prompts, or actual model tuning later?

Answer: The operator wants real tuning eventually, especially for local
mini-swarm models. However, a clean dataset/memory base must come first.
Fine-tuning on bad/noisy cycles would train wrong behavior.

Question: What should be stored?

Answer: The project already stores many things, but this must be audited. The
memory should include enough to understand:

- why the setup was entered;
- what parameters were used;
- expected movement;
- actual outcome;
- alternative exits;
- what worked;
- what failed;
- what validator/farm should learn.

Question: Store bad ideas too?

Answer: Yes. Bad trades and rejected/failed cases are important learning
material. Learning only from good examples is wrong for trading.

Question: Should there be ratings by strategy/pair/timeframe?

Answer: Yes. The system must know what is working, what is not, and where the
real edge is forming.

Question: What is the unit of learning?

Answer: A clean full cycle is better than just a single trade:

```text
setup -> variants -> validator grade -> paper result -> outcome analysis -> memory update
```

Bad trades are also results and must be studied.

Additional operator note: memory must be divided correctly by data circle and
model role. Avoid duplication. Each role should receive only the data it needs,
possibly through shared canonical storage/views.

## 7. Telegram and users

Question: Should Telegram distinguish calculated vs validated signals?

Answer: Yes. Cards should label signal type:

- calculated farm focus;
- experimental paper signal;
- PFR-validated signal;
- manual analysis.

Question: Can non-validated calculated signals be sent?

Answer: Yes, as focus/attention signals. The user/trader should understand
that they must think and manage risk themselves.

Question: Should weak signals be hidden?

Answer: Not yet clear. Signals come from farm and validator, and weakness is
often only known after outcome. The project should not claim 100% win rate.

Question: More signals or fewer stronger signals?

Answer: Quantity matters for manual signal trading and learning, but quality
also matters. Example target can be many signals with acceptable hit rate, not
one fake-perfect daily signal.

Question: Fast or long trades?

Answer: Both. If calculation shows a long position, show it. If it shows a
short/quick position, show it. The goal is to learn which systems work, not to
force one trading style.

## 8. Safety and live trading boundary

Question: Can LLM trade directly?

Answer: Not now. The hard rule remains: no live orders, no direct trade
authority. If future auto-trading is enabled, LLM should still propose actions
inside a controlled corridor, with deterministic risk/governor checks before
execution.

Question: Can LLM change farm parameters?

Answer: It can propose/operate inside its local research environment and must
show which parameters it used. It should not be limited to one static preset,
but changes must be recorded and checked by code/validator.

Question: What does "do not send outside" mean?

Answer:

- no raw private calculations to public GitHub;
- no `.env`, keys, raw private logs, or private strategies to cloud prompts;
- Telegram can show cards and public-facing analysis;
- local storage may contain more private working data.

Question: Can local models see more than cloud models?

Answer: Yes. Local models may have richer access through role boundaries.

## 9. Paper money and manual trades

Question: What paper capital model?

Answer:

- starting paper deposit: `700 USDT`;
- position size: about `30-40 USDT`;
- futures leverage: `3x-5x`.

Question: Should leverage be included?

Answer: Yes. Futures leverage is part of the intended model and should be
included carefully.

Question: Should human/manual trades be used?

Answer: Yes. Manual trades are valuable learning cases. They should be imported
or read for analysis only. The bot must not modify the real account. The
analyst should compare machine plan vs human execution.

Question: What about Excel journal?

Answer: The Excel journal is important. It has a direct connection to the real
account/API and should be inspected, cleaned, and prepared for recording
everything. It must be handled safely and not exposed publicly.

## 10. Main pain and implementation priority

Question: What is the biggest current pain?

Answer: Calculations and profitability. The engine "troits": parts exist, but
the calculation loop is not yet a coherent adaptive machine.

The priority is:

1. correct working architecture under continuous load;
2. learning loop;
3. trading calibration.

Until the base architecture is reliable, calibration has little value.

The next implementation should not start by tweaking one TP value. It should
first make the adaptive loop real:

```text
farm -> validator -> paper -> outcome -> memory -> next farm sweep
```

