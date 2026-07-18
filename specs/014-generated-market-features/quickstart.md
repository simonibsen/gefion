# Quickstart: Generated Market Features + Owner Gate

## The gate walk (Story 1)

```bash
# 1. Generate a candidate (explicitly, or a cycle does this for you)
gefion macro propose --principle breadth-confirms-trend
#    → candidate #7 (pending) — dry-run OK, sample values printed

# 2. Review the packet: code, inputs, provenance, dry-run
gefion macro candidate list                 # the pending queue
gefion macro candidate show --id 7          # the full packet

# 3. Decide
gefion macro candidate approve --id 7       # promotes into feature_functions
#    …or…
gefion macro candidate reject --id 7 --reason "duplicates breadth_sma200"

# 4. Nothing else to do: the nightly derive picks the approved series up
gefion macro derive                          # or wait for the cron
gefion macro list                            # the new series, with values
```

Pre-approval, every execution door refuses:

```bash
gefion macro derive --series my_candidate_series
# ✗ 'my_candidate_series' is a pending candidate — review with
#   `gefion macro candidate show`; it cannot compute values until approved.
```

## Composites (Story 2)

```bash
cat > risk_state.py <<'EOF'
def compute(row):
    # high vol + weak breadth + high dispersion = risk-off reading
    return row["vix"] / 20.0 - row["breadth_sma200"] + row["dispersion_20"]
EOF

gefion macro register-composite --name macro_risk_state \
    --series vix,breadth_sma200,dispersion_20 --body-file risk_state.py

gefion macro derive --series macro_risk_state --full   # full history
gefion macro derive                                     # nightly: incremental
```

Gap honesty: any date missing a stored value for ANY declared input gets no
output value. Cycles refuse at registration. Composites derive after their
inputs each night (topological order).

## Generated composites (Story 3)

```bash
gefion macro propose --principle vol-breadth-interaction --kind composite
# → composite candidate; same queue, same gate, same promotion
```
