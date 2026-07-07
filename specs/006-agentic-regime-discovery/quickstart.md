# Quickstart: Agentic Regime Discovery (006)

Assumes 005 shipped, services up, and (for real runs) production data present.

## 1. Pre-register and run a bounded discovery

```bash
cat > atoms.json <<'JSON'
{"atoms": [
  {"feature": "realized_vol_20", "form": "tercile"},
  {"feature": "indicator_adx_14", "form": "tercile"},
  {"feature": "indicator_rsi_14", "cmp": ">", "value": 70}
]}
JSON
gefion regime discover start --name first-hunt \
  --atoms atoms.json --depth 2 --budget 50 \
  --tier interaction --tier grammar \
  --universe-filter test_tickers,asset_type:common \
  --seed 42 --dataset nasdaq-full-20260706 --json
```

The run pre-registers everything (atoms, caps, seams, segregation) BEFORE evaluating;
the realized family size becomes the FDR denominator.

## 2. Read the whole story — including the losers

```bash
gefion regime discover show first-hunt --json        # pre-registration + family size
gefion regime discover ledger first-hunt --json      # every candidate, every verdict
gefion regime discover diagnostics first-hunt --json # limits hit; sample-dependent vs structural
gefion regime discover verdicts first-hunt --json    # FDR survivors (if any — most runs: none)
```

**Expect mostly rejections.** A discovery loop that admits often is broken; the negative-
control suite (CI) proves this loop admits nothing in pure noise.

## 3. An admitted regime is just a regime

```bash
gefion regime show <discovered-name>          # origin=machine, full provenance
gefion chart regime <discovered-name> --symbol SPY
gefion backtest run ... --by-regime <discovered-name>
gefion regime discover grades                 # trust accrues forward: probation = fold 1
```

## 4. Same three surfaces

MCP: `regime_discover_*` tools mirror each command. UI: Regimes → Discovery tab (runs,
ledgers, verdicts, grade timelines). Cycles: `gefion experiment propose --type
regime_discovery` runs discovery under cycle budgets (never auto-approved).
