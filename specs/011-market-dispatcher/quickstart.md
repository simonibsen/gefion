# Quickstart — Market Dispatcher (011)

```bash
# see market functions (scope column) and their bodies
gefion feat-fx-list --json | jq '.functions[] | select(.scope=="market")'

# compute (incremental) / recompute everything
gefion macro derive
gefion macro derive --full

# change breadth's threshold WITHOUT a deploy: edit the body in the DB,
# then recompute
psql "$DATABASE_URL" -c "UPDATE feature_functions SET function_body = ... WHERE name='breadth_sma200'"
gefion macro derive --series breadth_sma200 --full

# recover a mangled body from the repo seed (explicit, loud)
gefion macro derive --reseed breadth_sma200
```
