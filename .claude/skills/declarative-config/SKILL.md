---
name: declarative-config
description: Use when designing, extending, parsing, or validating strategy configuration - anything touching protocol.yaml, strategy condition lists, the condition vocabulary, or a request to make the config "more flexible". Defines the flat-AND condition model and the rule that expressiveness beyond it is a code change with a test, never a config feature.
---

# Declarative configuration

## The model

A strategy's conditions are a **flat list, combined with AND, drawn from a fixed vocabulary the
engine already knows.**

```yaml
conditions:
  - rsi_below: 30
  - price_above_sma: 200
  - volume_above_average: 1.5
```

Three conditions. All must hold. That is the entire semantics, and it is the whole point.

## What is not allowed

**No nesting.** No condition contains another condition. No groups, no sub-lists.

**No OR.** If two different condition sets are wanted, that is two strategies, and comparing
two strategies is something this project is built to do properly.

**No expressions.** No `"rsi < 30 and volume > avg * 1.5"`, no arithmetic, no interpolation, no
references to other keys, no templating. A value in the YAML is a number, a string, or a
boolean. It is never a thing to be evaluated.

**No conditionals.** No `if`, no `when`, no `unless`, no `else`. Nothing that branches.

## The vocabulary is closed

Every condition name is one the engine knows, is implemented in code, and has a test. Reading a
config never causes anything to be compiled, evaluated, or looked up dynamically. An unknown
condition name is a hard validation error at load time with the offending key named - never a
warning, never a silent skip. A config that half-loads is worse than one that refuses to load.

Validation happens once, up front, before any search starts. By the time a run begins, the
configuration is known to be valid in its entirety.

## When something more expressive is wanted

Add the capability to the engine as a **new named condition, in code, with a test**. Then it is
in the vocabulary and available to every config.

Do not add it as a config feature.

The reason is not taste. A configuration format that grows conditionals, expressions, and
nesting has become a programming language - one with no type checker, no debugger, no stack
traces, no tests, and no users but you. Every hour saved by not writing the engine change gets
repaid with interest the first time a strategy behaves unexpectedly and there is no way to step
through why.

There is a second reason specific to this project. Conditions that are flat, named, and finite
can be enumerated, counted, and searched systematically, and the size of the search space can be
stated honestly in the write-up. Conditions that are arbitrary expressions cannot, and any claim
about multiple-testing correction becomes unsupportable.

## What belongs in `protocol.yaml`

Parameters, not logic. Broker rates, date ranges, thresholds, fold counts, seeds - the numbers
that define the protocol.

These are **fixed before any search runs** and are never chosen at analysis time. That is the
central discipline of the whole project: a parameter picked after seeing results is not a
parameter, it is a degree of freedom, and it invalidates every statistic computed from it.

Market structure - how a fill price is computed, how commission is charged - belongs in code.
See the `cost-arithmetic` skill for that boundary; the formula is code, the rates are config.
