#!/bin/bash
# Register all 15 recommended derivative features using CLI
set -e

echo "Registering derivative features..."

# RSI Derivatives (3)
echo "RSI derivatives..."
g2 features-register --json --definition '{
  "name": "derivative_rsi_14_slope_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_rsi_14", "type": "slope", "window": 5, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_rsi_14_slope_10",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_rsi_14", "type": "slope", "window": 10, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_rsi_14_concavity_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_rsi_14", "type": "concavity", "window": 5, "method": "poly2"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

# MACD Derivatives (3)
echo "MACD derivatives..."
g2 features-register --json --definition '{
  "name": "derivative_macd_slope_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_macd", "type": "slope", "window": 5, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_macd_concavity_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_macd", "type": "concavity", "window": 5, "method": "poly2"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_macd_signal_slope_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_macd_signal", "type": "slope", "window": 5, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

# Price Derivatives (3)
echo "Price derivatives..."
g2 features-register --json --definition '{
  "name": "derivative_close_slope_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_close", "type": "slope", "window": 5, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_close_slope_10",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_close", "type": "slope", "window": 10, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_close_concavity_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_close", "type": "concavity", "window": 5, "method": "poly2"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

# ADX Derivatives (2)
echo "ADX derivatives..."
g2 features-register --json --definition '{
  "name": "derivative_adx_14_slope_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_adx_14", "type": "slope", "window": 5, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_adx_14_concavity_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_adx_14", "type": "concavity", "window": 5, "method": "poly2"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

# Stochastic Derivatives (2)
echo "Stochastic derivatives..."
g2 features-register --json --definition '{
  "name": "derivative_stoch_k_slope_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_stoch_k", "type": "slope", "window": 5, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_stoch_k_concavity_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_stoch_k", "type": "concavity", "window": 5, "method": "poly2"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

# Bollinger Band Derivatives (2)
echo "Bollinger Band derivatives..."
g2 features-register --json --definition '{
  "name": "derivative_bb_middle_slope_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_bb_middle", "type": "slope", "window": 5, "method": "linreg"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

g2 features-register --json --definition '{
  "name": "derivative_bb_middle_concavity_5",
  "function_name": "derivative",
  "params": {"source_feature": "indicator_bb_middle", "type": "concavity", "window": 5, "method": "poly2"},
  "source_table": "computed_features",
  "source_column": "value",
  "store_table": "computed_features",
  "store_column": "value",
  "store_type": "double precision",
  "active": true
}'

echo "✅ All 15 derivative features registered!"
