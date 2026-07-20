# Feature Specification: Modeling Universe Membership

**Feature Branch**: `015-modeling-universe`
**Created**: 2026-07-19
**Status**: Draft
**Input**: User description: "General universe membership: named, rule-defined subsets of the stock universe (universe definitions with generic field/operator/value predicates over entity attributes), date-aware interval membership materialization, a single chokepoint that all cross-section consumers (dataset builds, derived market series, experiment cross-sections) go through, universe provenance recorded in experiment and model artifacts, YAML export/import, CLI/MCP parity. First definitions exclude shell companies and ETFs from the default modeling universe."

## Why (context)

Roughly 9% of the tracked stock universe is shell companies (SPACs / blank-check
entities) and another ~20% is exchange-traded funds. Neither behaves like an
operating business, yet both currently flow into every cross-sectional
statistic the system computes — breadth series, dispersion, ranking features,
dataset builds, and experiment cross-sections. This pollutes the denominators
of measurements the discovery and experiment machinery depends on, and it does
so silently.

Today there is no concept of "which entities belong in the modeling
population." This feature introduces that concept as a first-class, named,
rule-defined object — a **universe** — following the same shape as regime
definitions (a regime is a named rule-defined subset of *dates*; a universe is
a named rule-defined subset of *entities*). Exclusion is expressed as rules
over entity attributes ("industry is X", "asset type is Y"), not as symbol
lists, so future additions require a definition change, never a code change.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Clean default modeling universe (Priority: P1)

The owner defines a default modeling universe whose rules exclude shell
companies and ETFs. Every cross-section consumer — dataset builds, derived
market series, experiment cross-sections — draws its population from this
universe through one shared gate, so the exclusions apply everywhere at once
and no consumer can accidentally bypass them.

**Why this priority**: This is the payoff that motivated the feature: cleaning
the denominator of every downstream statistic in one move. Without it the rest
is plumbing.

**Independent Test**: Define the default universe with the two initial rules,
refresh membership, build a dataset and a breadth series, and verify no shell
company or ETF appears in either population — while a control run against an
"everything" universe still includes them.

**Acceptance Scenarios**:

1. **Given** a default universe with rules excluding industry "SHELL COMPANIES" and asset type "ETF", **When** membership is refreshed, **Then** every active shell company and ETF is marked excluded with the name of the rule that excluded it, and all other stocks remain members.
2. **Given** a refreshed default universe, **When** a dataset build or derived market series computation runs without naming a universe, **Then** its population is exactly the default universe's members and the result records which universe was used.
3. **Given** a symbol excluded by a rule, **When** the owner asks why that symbol is not in the universe, **Then** the answer names the specific rule and its reason text.
4. **Given** a new stock lists next month and its industry classifies it as a shell company, **When** nightly membership refresh runs, **Then** it is excluded automatically with no human action and no definition change.

---

### User Story 2 - Add a new exclusion without writing code (Priority: P2)

Later, the owner (or an operating session, subject to the owner gate) decides
penny stocks below a price floor should also be excluded. They add one rule to
the universe definition — a generic predicate of field, operator, and value —
refresh membership, and every consumer picks up the change. No code changes,
no schema changes.

**Why this priority**: Generality is the explicit design goal — the first two
rules are just the first two. The feature fails its purpose if the third rule
requires engineering work.

**Independent Test**: Add a rule with a numeric threshold predicate (e.g.
price below 1.00) to an existing universe, refresh, and verify matching
symbols leave membership and the change is visible to consumers — then remove
the rule and verify they return.

**Acceptance Scenarios**:

1. **Given** an existing universe, **When** a rule with a supported operator (equals, not-equals, in-list, greater/less-than, between, is-missing) over a supported entity attribute is added and membership refreshed, **Then** matching symbols are excluded without any code change.
2. **Given** a rule edit, **When** membership is refreshed, **Then** the universe's definition fingerprint changes, and prior results recorded against the old fingerprint remain attributable to it.
3. **Given** a proposed rule referencing an unknown attribute or unsupported operator, **When** it is submitted, **Then** it is refused with a message naming the valid attributes and operators (refusal, not silent acceptance).

---

### User Story 3 - Date-aware membership for honest backtests (Priority: P3)

Rules over time-varying attributes (price, liquidity, market capitalization)
produce membership that changes over time. Membership is stored as intervals
(symbol entered on date A, exited on date B), and any consumer can ask for the
universe **as of** a historical date, so a backtest over 2015 uses 2015
membership, not today's.

**Why this priority**: Without as-of correctness, threshold rules would
silently apply today's membership to decades of history — a look-ahead bias
the system's statistical honesty rules exist to prevent. Static rules (shells,
ETFs) degrade gracefully without it; threshold rules do not.

**Independent Test**: Define a universe with a price-floor rule, materialize
membership over a historical window containing a symbol that crossed the
threshold, and verify as-of queries on either side of the crossing date return
different membership — and that repeating the materialization yields identical
intervals.

**Acceptance Scenarios**:

1. **Given** a symbol whose price crossed the rule threshold on date D, **When** membership is materialized over history, **Then** as-of queries before and after D differ accordingly, and the interval boundaries are stable across repeated materializations.
2. **Given** a static-attribute rule (industry, asset type), **When** membership is materialized, **Then** it produces a single open-ended interval per excluded symbol (no daily churn).
3. **Given** an attribute with no recorded history (e.g. industry classification), **When** historical membership is evaluated, **Then** the current value is applied across time and this limitation is stated in the universe's documentation and inspection output.

---

### User Story 4 - Universe provenance in results (Priority: P4)

Every dataset, experiment, and model artifact records which universe (name and
definition fingerprint) its cross-section came from. Two experiments run
against different universes are visibly different experiments; a result
produced before this feature existed is distinguishable from one produced
after.

**Why this priority**: The experiment framework's credibility rests on knowing
what population a result was measured on. Same rationale as device provenance
(#146): reproduction requires recording what actually ran.

**Independent Test**: Run the same experiment against two universes and verify
their stored results carry different universe identities; verify a model
artifact round-trips its universe stamp through save and load.

**Acceptance Scenarios**:

1. **Given** an experiment run, **When** its results are stored, **Then** they include the universe name and definition fingerprint used for the cross-section.
2. **Given** a saved model artifact, **When** it is loaded, **Then** its universe identity is available alongside its other provenance (algorithm, device, data window).
3. **Given** a universe definition that changed between two runs, **When** their results are compared, **Then** the differing fingerprints make the population change visible.

---

### Edge Cases

- **Missing attributes**: A stock with no industry/asset-type value (e.g. some delisted symbols) is NOT silently excluded — absence of data is not evidence of exclusion. Excluding unclassified symbols requires an explicit is-missing rule so the choice is visible and owned.
- **Delisted stocks**: Remain part of historical membership for the periods they were alive and matching; universes filter *kind of entity*, not *liveness*. (Liveness is handled by price data existing at all.)
- **Per-symbol overrides**: A pin (force-include / force-exclude with mandatory reason) wins over rules, is rare by design, and is stored and versioned with the definition.
- **Empty or near-empty universe**: A refresh that would produce an empty universe, or shrink membership by more than a guard fraction in one step, refuses and reports rather than silently gutting every downstream consumer (same "refuse loudly" idiom as the deletion doors and gate refusals).
- **Disabled or missing universe**: A consumer naming a disabled/nonexistent universe gets a refusal naming valid universes — never a silent fallback to "everything".
- **Deletion**: A universe referenced by stored results/artifacts refuses deletion through the standard deletion-door pattern (dry-run default, dependency listing); definitions supersede rather than mutate history.
- **Rule churn/flapping**: A threshold rule on a noisy attribute may cause a symbol to enter/exit repeatedly; v1 accepts this (intervals record it honestly) and the inspection output surfaces flap counts so a hysteresis rule can be considered later.
- **Conflicting rules**: Exclusion rules combine as "any match excludes" (OR). Pins beat rules. There is no rule-vs-rule precedence to reason about.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST support multiple named universe definitions, each with a description, enabled flag, an ordered list of rules, and optional per-symbol pins.
- **FR-002**: A rule MUST be a generic predicate — attribute, operator (equals, not-equals, in-list, greater-than-or-equal, less-than-or-equal, between, is-missing), value — over a documented set of entity attributes (at minimum: asset type, industry, sector, exchange, listing status, and available time-varying attributes such as price and market capitalization). Adding a rule MUST NOT require code or schema changes.
- **FR-003**: Rules and pins MUST carry a human-readable reason; every exclusion MUST be traceable to the rule or pin that caused it.
- **FR-004**: Membership MUST be materialized as date intervals per symbol, refreshed as part of the nightly chain, and queryable as of any date. Static-attribute exclusions MUST produce single open-ended intervals.
- **FR-005**: There MUST be exactly one membership gate through which all cross-section consumers obtain their population: dataset builds, derived market series (breadth, dispersion, sector/industry series), and experiment/discovery cross-sections. Consumers MUST declare their universe, defaulting to the default modeling universe.
- **FR-006**: Data ingestion, data-quality scanning, and raw price storage MUST NOT be filtered by universe membership — the system observes everything and models a subset.
- **FR-007**: Each universe definition MUST have a stable content fingerprint that changes when and only when its rules/pins change; results and artifacts (datasets, experiments, models) MUST record the universe name and fingerprint they were produced under.
- **FR-008**: Universe definitions MUST round-trip through human-readable export/import (consistent with existing definition export/import), and every capability MUST have CLI and MCP parity: define/update, list, show (including rules and reasons), members (with as-of), explain-symbol, refresh, enable/disable, export/import, delete.
- **FR-009**: Universe deletion MUST follow the standard deletion-door pattern: dry-run by default, dependency enumeration, refusal when referenced by stored results, and no erasure of provenance records.
- **FR-010**: A refresh producing an empty universe, or a single-step membership shrink beyond a guard threshold, MUST refuse and report rather than apply.
- **FR-011**: The default modeling universe MUST ship with three initial rules — exclude industry "SHELL COMPANIES", exclude asset type ETF, and exclude sub-dollar closes (time-varying; owner-approved 2026-07-19 during implementation) — and the system MUST report headline universe counts (members, excluded, by rule) in system health output.
- **FR-012**: Universe operations MUST be observable via the standard tracing conventions, and user documentation plus the learning curriculum MUST cover the universe concept in the same increment.
- **FR-013**: When the default universe first applies (and after any later rule change affecting derived-series populations), the full history of affected derived market series MUST be recomputed under the new membership — no mid-series population discontinuities. Regime labels conditioned on recomputed series MUST re-derive, and previously admitted signals whose conditioning series changed (e.g. the sector-breadth signal from 013) MUST be re-checked through the existing re-verdict machinery. The recomputation MUST be recorded as a vintage change (old fingerprint → new fingerprint) rather than silently overwriting provenance.

### Key Entities

- **Universe Definition**: A named, versioned, enabled/disabled set of rules and pins with descriptions and reasons. Identity = name; content identity = fingerprint.
- **Rule**: A single attribute/operator/value predicate with a reason. Belongs to exactly one universe definition. "Any exclude-rule match excludes."
- **Pin**: A per-symbol force-include/force-exclude override with a mandatory reason; beats rules.
- **Membership Interval**: The materialized fact "symbol S was a member of universe U from date A to date B (or open-ended)", plus, for exclusions, which rule/pin caused it.
- **Attribute Surface**: The documented set of entity attributes rules may reference — static identity attributes and time-varying market attributes the system already stores.
- **Provenance Stamp**: Universe name + fingerprint recorded on datasets, experiment results, and model artifacts.

## Out of Scope (v1)

- Rank-based membership ("top 1000 by dollar volume") and scheduled reconstitution — the definition model leaves room; v1 supports threshold predicates only.
- Hysteresis / anti-flapping rules (surfaced via flap counts first; designed later if needed).
- Historical attribute vintages for attributes the system stores only as current state (industry, sector): v1 applies current classification across time and says so.
- Universes over non-stock entities (macro series, sectors as entities).
- ~~Retroactive re-derivation~~ **Resolved (owner, 2026-07-19)**: derived-series history IS recomputed — see FR-013.

## Automation *(consider)*

- **Proposed skill**: None needed.
- **Rationale**: Universe operations are infrequent owner-level actions already covered by CLI/MCP parity; the existing `/gefion` routing docs gain a section, which is documentation rather than a new skill.

## Assumptions

- The base population a universe filters is the full tracked stock universe (all listed and delisted equities with price history); ETFs are entities of the same kind and are excluded by rule, not by construction.
- The two initial rules operate on static attributes, so v1 delivers immediate value even before any time-varying rule exists; date-aware machinery (P3) is built so threshold rules are honest from their first use.
- Existing consumers currently implementing ad-hoc population logic (e.g. sector series using sector-tagged members) migrate onto the chokepoint without changing their *intended* populations beyond the new exclusions.
- The ~22% of active stocks currently missing industry/sector classification are unaffected by the two initial rules (no attribute → no match → retained); closing that coverage gap is separate, already-identified ingestion work that this feature benefits from but does not depend on.
- Owner gate: universe definitions are owner-controlled objects (like feature definitions and regime definitions); operating sessions may propose changes but activation follows the same review discipline as other definition changes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After the default universe ships, 100% of shell-company and ETF symbols are absent from newly built datasets, newly derived market-series populations, and new experiment cross-sections — verifiable by inspection commands without reading code.
- **SC-002**: Adding a new exclusion rule end-to-end (define → refresh → visible in consumers) takes under 10 minutes of owner effort and zero code changes.
- **SC-003**: For any symbol, "why is/isn't this in universe X (as of date D)?" is answerable with one command, naming the rule or pin and its reason.
- **SC-004**: Membership materialization is deterministic: repeating a refresh against unchanged data and definition produces identical intervals and an identical fingerprint.
- **SC-005**: Every new dataset, experiment result, and model artifact records a universe identity; results predating the feature are distinguishable from results produced under a named universe.
- **SC-006**: Nightly refresh of the default universe adds no more than 5 minutes to the nightly chain.
- **SC-007**: A universe that would empty or shrink past the guard threshold in one refresh is refused, and the refusal is visible in system health output.
