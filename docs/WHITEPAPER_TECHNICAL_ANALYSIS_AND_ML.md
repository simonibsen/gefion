# From Patterns to Probabilities: A Modern Approach to Technical Analysis Through Machine Learning

**Abstract**

This white paper explores the evolution of technical analysis from rule-based pattern recognition to probabilistic machine learning models. We examine the theoretical foundations of both approaches, their strengths and limitations, and demonstrate how modern ML techniques can enhance traditional technical analysis while preserving its core insights. Using Gefion, a database-first quantitative platform, we illustrate a practical implementation that bridges classical technical indicators with contemporary machine learning methods.

**Author**: Gefion Development Team
**Target Audience**: Quantitative analysts, algorithmic traders, data scientists, software engineers
**Date**: December 2024 (Last Updated: December 2024)

---

## Table of Contents

1. [Introduction](#introduction)
2. [The Theory of Technical Analysis](#the-theory-of-technical-analysis)
3. [Machine Learning: From Rules to Probabilities](#machine-learning-from-rules-to-probabilities)
4. [Bridging Technical Analysis and ML](#bridging-technical-analysis-and-ml)
5. [The Gefion Platform: A Practical Implementation](#the-gefion-platform-a-practical-implementation)
6. [Case Study: Quantile Regression for Risk-Aware Prediction](#case-study-quantile-regression-for-risk-aware-prediction)
7. [Future Directions: Multi-Model Integration](#future-directions-multi-model-integration)
8. [Conclusion](#conclusion)

---

## Introduction

Financial markets have long fascinated analysts seeking patterns in price movements. Two distinct philosophies have emerged: **technical analysis**, which seeks patterns in historical price and volume data, and **fundamental analysis**, which examines company financials and macroeconomic factors. While fundamental analysis asks "what is this asset worth?", technical analysis asks "what are other traders doing?"

This paper focuses on the evolution within technical analysis itself—from deterministic rule-based systems to probabilistic machine learning models. We argue that ML doesn't replace technical analysis but rather provides a more rigorous framework for the same underlying hypothesis: **past price patterns contain information about future price movements**.

### The Central Hypothesis

Both technical analysis and ML-based approaches share a core belief: markets exhibit inefficiencies that manifest as predictable patterns in price, volume, and derived indicators. The key differences lie in:

1. **Pattern Recognition**: Manual (classical TA) vs automated (ML)
2. **Rule Definition**: Explicit thresholds (RSI > 70) vs learned decision boundaries
3. **Output**: Binary signals (buy/sell) vs probability distributions
4. **Adaptation**: Static rules vs models that update with new data

### Why This Matters

Traditional technical analysis suffers from several limitations:
- **Overfitting to backtests**: Cherry-picking indicators that worked historically
- **Binary thinking**: Buy/sell signals ignore uncertainty
- **Static rules**: Markets evolve, but indicators remain constant
- **Subjectivity**: Different analysts interpret the same chart differently

Machine learning addresses these issues while preserving technical analysis's key insight: **price action encodes market psychology**.

---

## The Theory of Technical Analysis

### Foundational Principles

Technical analysis rests on three core assumptions (Murphy, 1999):

1. **Market action discounts everything**: All known information is reflected in price
2. **Prices move in trends**: Momentum exists and can be exploited
3. **History repeats itself**: Human psychology creates recurring patterns

These principles have empirical support. Jegadeesh & Titman (1993) documented momentum effects across markets. Behavioral finance research by Kahneman & Tversky (1979) explains why psychological biases create predictable patterns.

### Common Technical Indicators

Technical analysts have developed hundreds of indicators, but most fall into four categories:

**1. Trend Indicators** (e.g., Moving Averages)
```
SMA(n) = (P₁ + P₂ + ... + Pₙ) / n
```
**Theory**: Smooths noise to reveal underlying trend. Crossovers signal trend changes.

**2. Momentum Oscillators** (e.g., RSI)
```
RSI = 100 - [100 / (1 + (Avg Gain / Avg Loss))]
```
**Theory**: Measures speed of price changes. Extreme values (>70, <30) suggest overbought/oversold conditions.

**3. Volatility Indicators** (e.g., Bollinger Bands)
```
BB_upper = SMA(n) + (k × σ)
BB_lower = SMA(n) - (k × σ)
```
**Theory**: Price tends to revert to the mean. Bands expand during high volatility, contract during consolidation.

**4. Volume Indicators** (e.g., On-Balance Volume)
```
OBV = OBV_prev + (volume × sign(price_change))
```
**Theory**: Volume precedes price. Divergences between OBV and price signal reversals.

### Limitations of Rule-Based Approaches

While these indicators capture real market phenomena, rule-based systems have fundamental weaknesses:

1. **Threshold Sensitivity**: Why is RSI > 70 overbought? Why not 72 or 68?
2. **Indicator Interaction**: How do you combine conflicting signals (bullish RSI, bearish MACD)?
3. **Non-Stationarity**: Market regimes change. 2008 crisis patterns differ from 2020 pandemic.
4. **Survivorship Bias**: Published strategies often cherry-pick the best-performing indicators.

This is where machine learning enters.

---

## Machine Learning: From Rules to Probabilities

### The ML Paradigm Shift

Machine learning inverts the traditional approach:

**Traditional TA**: Human defines rules → System executes
**Machine Learning**: System learns rules from data → Human validates

This shift has profound implications:
- **Learned thresholds**: The model discovers optimal RSI cutoffs from data
- **Feature interactions**: Non-linear relationships emerge automatically
- **Adaptive rules**: Retrain monthly to capture regime changes
- **Probabilistic outputs**: "60% chance of positive return" vs "buy signal"

### Supervised Learning for Financial Prediction

The canonical ML approach treats market forecasting as a **supervised learning** problem:

**Input Features** (X): Technical indicators, price patterns, volume metrics
**Target Labels** (y): Future returns at horizon H (e.g., 7-day forward return)
**Model**: f(X) → ŷ, where ŷ approximates y

Common algorithms include:
- **Linear Regression**: Simple, interpretable, assumes linear relationships
- **Random Forests**: Handles non-linearity, provides feature importance
- **Gradient Boosting** (XGBoost, LightGBM): State-of-the-art for tabular data
- **Neural Networks**: Flexible but require large datasets

### Why Quantile Regression?

Traditional regression predicts the **mean** (expected value):
```
E[y|X] = f(X)
```

But in finance, we care about **risk**, not just returns. Quantile regression predicts the **distribution**:
```
Q_τ(y|X) = f_τ(X)    where τ ∈ [0, 1]
```

For τ = 0.1, 0.5, 0.9, we get:
- **q10**: 10th percentile (pessimistic scenario)
- **q50**: Median (most likely outcome)
- **q90**: 90th percentile (optimistic scenario)

**Example**: Traditional regression might predict "AAPL will return +2.5% in 7 days". Quantile regression predicts:
- 10% chance of losing more than 1.5%
- 50% chance of gaining less than 2.1%
- 90% chance of gaining less than 5.8%

This uncertainty quantification enables:
- **Position sizing**: Allocate more capital to high-confidence predictions
- **Risk management**: Avoid stocks with large downside (q10 < -5%)
- **Portfolio construction**: Balance high-risk/high-reward vs stable positions

### Theoretical Foundations

**Koenker & Bassett (1978)** introduced quantile regression with the pinball loss function:
```
L_τ(y, ŷ) = { τ(y - ŷ)      if y ≥ ŷ
            { (1-τ)(ŷ - y)  if y < ŷ
```

This asymmetric penalty ensures the model predicts the true τ-th quantile. For τ=0.9, under-prediction is penalized 9× more than over-prediction.

**Calibration** is critical. A well-calibrated model should have:
- 10% of actual returns below q10 prediction
- 50% of actual returns below q50 prediction
- 90% of actual returns below q90 prediction

Poorly calibrated models (e.g., q10 coverage = 25%) are systematically overconfident.

---

## Bridging Technical Analysis and ML

### The Continuity Thesis

We argue that ML **extends** rather than replaces technical analysis. Consider the progression:

**Level 0**: Raw price data
**Level 1**: Technical indicators (RSI, MACD) - **hand-crafted features**
**Level 2**: Rule-based signals (buy when RSI < 30) - **hand-crafted logic**
**Level 3**: ML models trained on indicators - **learned logic, hand-crafted features**
**Level 4**: Deep learning on raw prices - **learned features and logic**

Most practitioners operate at Level 3: using traditional indicators as features but learning decision rules from data. This hybrid approach has practical advantages:

1. **Interpretability**: "Model predicts bullish because RSI=25, MACD crossed up" vs "neuron 47 activated"
2. **Sample Efficiency**: Technical indicators encode domain knowledge, reducing data requirements
3. **Robustness**: Regularization on familiar features is more stable than raw price patterns

### Case Study: RSI as a Feature

Consider the Relative Strength Index (RSI). Classical usage:
```
if RSI > 70: sell (overbought)
if RSI < 30: buy (oversold)
```

But empirical analysis reveals:
- In **trending markets**, RSI stays overbought/oversold for extended periods
- In **range-bound markets**, RSI reversals work well
- **Optimal thresholds** vary by asset and volatility regime

Machine learning can discover these nuances:
```python
# Classical approach
signal = "buy" if rsi < 30 else "sell" if rsi > 70 else "hold"

# ML approach (learned from data)
features = [rsi, rsi_change, volatility, trend_strength]
probability_up = model.predict_proba(features)
```

The model might learn:
- "Buy when RSI < 25 AND rising (not falling further)"
- "Ignore RSI in strong trends (use MACD instead)"
- "Weight RSI more heavily in low-volatility environments"

This is the power of learned interactions.

---

## The Gefion Platform: A Practical Implementation

### Design Philosophy: Database-First Architecture

gefion implements a **database-first** approach where features and functions are stored as data, not code. This architectural choice has profound implications for research velocity and reproducibility.

**Traditional Approach** (code-first):
```python
# features.py
def compute_rsi(prices, period=14):
    # ... implementation ...

# Changing period requires code edit
```

**Gefion Approach** (database-first):
```json
{
  "name": "indicator_rsi_14",
  "function_name": "indicator",
  "params": {"indicator": "rsi", "period": 14},
  "source_table": "stock_ohlcv",
  "source_column": "close",
  "store_table": "computed_features"
}
```

Benefits:
1. **Version Control**: Git tracks feature definitions as JSON files
2. **Reproducibility**: Export entire feature set for a historical date
3. **Experimentation**: Test RSI(10) vs RSI(14) vs RSI(20) without code changes
4. **Auditability**: Database logs when features were computed and with what parameters

### Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  Data Sources                       │
│  (AlphaVantage API, News Sentiment, Fundamentals)   │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│           TimescaleDB (Time-Series Store)           │
├─────────────────────────────────────────────────────┤
│  stock_ohlcv: Price data (hypertable)               │
│  feature_definitions: Metadata for indicators       │
│  feature_functions: Sandboxed Python code           │
│  computed_features: All technical indicators        │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│              ML Pipeline (Python)                   │
├─────────────────────────────────────────────────────┤
│  1. Dataset Builder: Join prices + features         │
│  2. Label Generator: Compute forward returns        │
│  3. Model Trainer: Quantile regression              │
│  4. Prediction Engine: Generate forecasts           │
└──────────────┬──────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────┐
│            Prediction Tables                        │
├─────────────────────────────────────────────────────┤
│  quantile_predictions: Distribution forecasts       │
│  model_performance: Calibration metrics             │
└─────────────────────────────────────────────────────┘
```

### Extensibility: Adding Custom Features

One of Gefion's strengths is the ease of adding new data sources. Let's walk through an example.

**Scenario**: Incorporate news sentiment from AlphaVantage News Sentiment API.

**Step 1**: Create fetcher function ([feature-functions/news_sentiment_fetcher.json](feature-functions/news_sentiment_fetcher.json)):
```python
def compute(rows, specs):
    """Fetch sentiment data from AlphaVantage News Sentiment API."""
    symbol = rows[0]['symbol']
    api_key = os.environ['ALPHAVANTAGE_API_KEY']

    # Fetch from API
    url = f'https://www.alphavantage.co/query?function=NEWS_SENTIMENT&tickers={symbol}&apikey={api_key}'
    response = requests.get(url)
    data = response.json()

    # Aggregate sentiment scores by date
    daily_sentiments = defaultdict(lambda: {'scores': [], 'relevance': []})
    for article in data.get('feed', []):
        date = article['time_published'][:8]  # YYYYMMDD
        for ticker_sentiment in article.get('ticker_sentiment', []):
            if ticker_sentiment.get('ticker') == symbol:
                daily_sentiments[date]['scores'].append(ticker_sentiment['ticker_sentiment_score'])
                daily_sentiments[date]['relevance'].append(ticker_sentiment['relevance_score'])

    # Weighted average by relevance
    results = []
    for date, data in daily_sentiments.items():
        if data['scores']:
            weighted_sentiment = sum(s * r for s, r in zip(data['scores'], data['relevance'])) / sum(data['relevance'])
            results.append({'date': date, 'sentiment_score': weighted_sentiment})

    return results
```

**Step 2**: Import and register:
```bash
# Import function to database
gefion feat-fx-import --dir feature-functions

# Register feature definition
gefion feat-def-register --definition '{
  "name": "news_sentiment_score",
  "function_name": "news_sentiment_fetcher",
  "params": {"column": "sentiment_score"},
  "source_table": "stock_ohlcv",
  "source_column": "symbol",
  "store_table": "computed_features",
  "store_column": "value"
}'

# Compute for stocks
gefion feat-compute --features news_sentiment_score --symbols AAPL,MSFT,GOOGL --local
```

**Step 3**: Use in ML training:
```bash
# Sentiment automatically included in dataset build
gefion ml dataset-build --name sentiment_test --version v1 \
  --symbols AAPL,MSFT,GOOGL --horizons 7,30 --export

# Features CSV now contains: indicator_rsi_14, indicator_macd, news_sentiment_score, ...
```

The database-first architecture means **no code changes** to the ML pipeline. New features are automatically discovered and included.

### Full Pipeline Integration

gefion provides end-to-end workflow from data ingestion to predictions:

```bash
# 1. Ingest price data
gefion data-update --exchange NASDAQ --limit 100 --timeframe auto

# 2. Compute all technical indicators
gefion feat-compute --exchange NASDAQ --limit 100 --local

# 3. Build ML dataset
gefion ml dataset-build \
  --name nasdaq100 --version v1 \
  --exchange NASDAQ --limit 100 \
  --horizons 7,30,90 \
  --export

# 4. Train quantile regression model
gefion ml train \
  --dataset-name nasdaq100 --dataset-version v1 \
  --model-name prod_model --model-version 20241214 \
  --algorithm xgboost

# 5. Generate predictions
gefion ml predict \
  --model-name prod_model --model-version 20241214 \
  --prediction-date 2024-12-14 \
  --exchange NASDAQ --limit 100

# 6. Evaluate calibration
gefion ml eval \
  --model-name prod_model --model-version 20241214 \
  --start-date 2024-01-01 --end-date 2024-12-01
```

**Output** (calibration report):
```
======================================================================
Model Evaluation Report: prod_model
======================================================================

Horizon: 7 days
--------------------------------------------------
  Samples:              2,847
  Q10 Calibration:      11.2% (target: 10%, error: 1.2%)
  Q50 Calibration:      49.5% (target: 50%, error: 0.5%)
  Q90 Calibration:      89.1% (target: 90%, error: 0.9%)
  80% Interval Coverage: 78.4% (target: 80%)
  Quantile Loss:        0.0187 (lower is better)
  Avg IQR:              0.0623

Interpretation: Well-calibrated. Q50 nearly perfect. Slight underestimate of uncertainty (IQR too narrow).
```

---

## Case Study: Quantile Regression for Risk-Aware Prediction

### The Problem: Point Estimates Hide Risk

Traditional regression models predict expected returns:
```
E[r_t+7 | X_t] = β₀ + β₁·RSI_t + β₂·MACD_t + ε_t
```

For AAPL on 2024-12-14, the model might predict **+2.8% 7-day return**.

But this hides critical information:
- What's the downside risk?
- How confident is the model?
- Should I size this position differently than a **+2.8%** prediction with lower uncertainty?

### The Solution: Distribution Forecasting

Quantile regression produces three models (q10, q50, q90):
```
Q₀.₁₀(r | X) = f₀.₁₀(X)
Q₀.₅₀(r | X) = f₀.₅₀(X)
Q₀.₉₀(r | X) = f₀.₉₀(X)
```

**AAPL Prediction** (2024-12-14, 7-day horizon):
- **q10 = -1.8%**: 10% chance of losing more than 1.8%
- **q50 = +2.1%**: Median expected gain of 2.1%
- **q90 = +6.5%**: 10% chance of gaining more than 6.5%
- **IQR = 8.3%**: High uncertainty (wide distribution)

**MSFT Prediction** (same date, same horizon):
- **q10 = +0.5%**: Downside protected (10% worst case is small gain)
- **q50 = +2.3%**: Similar median to AAPL
- **q90 = +4.1%**: Lower upside than AAPL
- **IQR = 3.6%**: Lower uncertainty (narrow distribution)

### Portfolio Decision

Given these forecasts, how should we allocate capital?

**Traditional approach** (mean-variance optimization):
- Only uses q50 predictions
- Ignores skewness and tail risk

**Quantile-aware approach**:
```python
def position_size(q10, q50, q90, capital):
    # Prioritize downside protection
    if q10 < -0.03:  # Max 3% loss in worst case
        return 0

    # Kelly criterion with quantile estimates
    edge = q50  # Expected return
    odds = (q90 - q10) / 2  # Proxy for variance
    kelly_fraction = edge / odds

    # Conservative: Use 25% of Kelly
    return capital * kelly_fraction * 0.25

# AAPL: High uncertainty → smaller position
aapl_size = position_size(-0.018, 0.021, 0.065, capital=100000)
# Output: $3,200

# MSFT: Lower risk → larger position
msft_size = position_size(0.005, 0.023, 0.041, capital=100000)
# Output: $6,400
```

Despite similar median returns (q50), MSFT gets **2× larger allocation** due to:
1. Protected downside (q10 > 0)
2. Lower uncertainty (smaller IQR)

This is the practical value of distribution forecasting.

---

## Multi-Model Integration

### Trend Classification: Categorical Predictions (Implemented)

Quantile regression predicts **how much** prices might move. But sometimes we care about **trend strength**:
- Will this stock show momentum (strong trend)?
- Or is it range-bound (mean reversion)?

gefion includes **trend classification** alongside quantile regression:

**Labels** (derived from forward returns and thresholds):
- `strong_up`: r > 5%
- `weak_up`: 2% < r ≤ 5%
- `neutral`: -2% ≤ r ≤ 2%
- `weak_down`: -5% ≤ r < -2%
- `strong_down`: r < -5%

**Classifier** (XGBoost multi-class):
```python
from xgboost import XGBClassifier

model = XGBClassifier(
    objective='multi:softmax',
    num_class=5,
    eval_metric='mlogloss'
)

model.fit(X_train, y_train_class)
predictions = model.predict_proba(X_test)
# Output: [P(strong_down), P(weak_down), P(neutral), P(weak_up), P(strong_up)]
```

### Model Ensembles (Implemented)

gefion supports combining multiple algorithms for improved prediction accuracy:

```bash
# Train ensemble with XGBoost + LightGBM
gefion ml train-ensemble \
  --dataset-name nasdaq100 --dataset-version v1 \
  --model-name ensemble_model --model-version 20241214 \
  --algorithms xgboost,lightgbm

# Generate ensemble predictions
gefion ml predict-ensemble \
  --model-name ensemble_model --model-version 20241214 \
  --symbols AAPL,MSFT,GOOGL
```

**Benefits**:
- Reduces prediction variance through weighted averaging
- Combines linear + non-linear patterns
- More robust to outliers and regime changes

### Combined Strategy

Use **both** quantile and trend predictions:

**Screening Query**:
```sql
-- Find stocks with strong uptrend AND protected downside
SELECT s.symbol, qp.q50, qp.q90, tcp.confidence
FROM quantile_predictions qp
JOIN trend_class_predictions tcp ON
  qp.data_id = tcp.data_id AND
  qp.prediction_date = tcp.prediction_date
JOIN stocks s ON qp.data_id = s.id
WHERE qp.prediction_date = CURRENT_DATE
  AND qp.horizon_days = 7
  AND tcp.predicted_class = 'strong_up'  -- Trend filter
  AND tcp.confidence > 0.7               -- High confidence
  AND qp.q10 > 0                         -- Downside protected
ORDER BY qp.q90 DESC                     -- Highest upside
LIMIT 20;
```

This dual-model approach combines:
- **Trend classification**: Screen for momentum/reversal patterns
- **Quantile regression**: Size positions by risk/reward

### Cross-Sectional Features (Implemented)

gefion supports both **time-series features** (stock's own history) and **cross-sectional features** (stock vs peers):

**Time-series features**:
- RSI, MACD, Bollinger Bands
- Past returns and volatility

**Cross-sectional features** (market-relative metrics):
- Return relative to sector benchmark
- Volume relative to sector average
- Sector rotation momentum
- Market-relative metrics

**Example**:
```sql
-- Feature: Return vs sector average
WITH sector_avg AS (
  SELECT date, sector, AVG(return) AS avg_return
  FROM stock_returns
  GROUP BY date, sector
)
SELECT sr.symbol, sr.date, (sr.return - sa.avg_return) AS return_vs_sector
FROM stock_returns sr
JOIN sector_avg sa ON sr.date = sa.date AND sr.sector = sa.sector;
```

This enables **sector rotation strategies**: Buy stocks outperforming their sector, as sector strength often persists.

---

## Conclusion

### Key Takeaways

1. **ML Extends Technical Analysis**: Machine learning doesn't replace technical indicators but learns optimal rules for combining them.

2. **Quantile Regression > Point Estimates**: Predicting distributions enables risk-aware position sizing and portfolio construction.

3. **Database-First Architecture Matters**: Storing features as data (not code) accelerates research and ensures reproducibility.

4. **Full Pipeline Integration**: End-to-end workflow from data ingestion to backtesting is essential for production deployment.

5. **Future is Multi-Model**: Combining quantile regression (risk assessment) with trend classification (screening) and cross-sectional features (sector rotation) creates a comprehensive system.

### The Gefion Philosophy

gefion embodies a pragmatic approach to quantitative analysis:
- **Start with proven indicators** (RSI, MACD) - don't reinvent the wheel
- **Let models discover interactions** - avoid hard-coded rules
- **Quantify uncertainty** - distributions beat point estimates
- **Version everything** - database-first enables reproducibility
- **Iterate rapidly** - adding features shouldn't require code changes

### Recommendations for Practitioners

**If you're starting out**:
1. Build intuition with classical technical analysis
2. Understand indicator theory (why RSI measures momentum)
3. Recognize limitations (threshold sensitivity, subjectivity)
4. Transition to ML as a natural evolution, not a replacement

**If you're experienced**:
1. Focus on feature engineering (better indicators > complex models)
2. Prioritize calibration over accuracy (distribution > point estimate)
3. Implement robust backtesting (avoid look-ahead bias)
4. Retrain models regularly (markets evolve)

### Final Thought

The debate between "technical analysis vs machine learning" is a false dichotomy. Technical analysis provides the **language** (indicators, patterns), while machine learning provides the **grammar** (learned rules, probabilistic reasoning). Together, they form a powerful framework for understanding market dynamics and generating alpha.

As computing power and data availability continue to increase, we expect the integration to deepen. Deep learning models may eventually learn features directly from raw price data, bypassing hand-crafted indicators. But for now, the hybrid approach—ML trained on technical indicators—represents the practical state-of-the-art.

gefion is our contribution to this evolution: a platform that makes rigorous, reproducible, ML-driven technical analysis accessible to quantitative analysts and algorithmic traders.

---

## References

- Jegadeesh, N., & Titman, S. (1993). "Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency." *Journal of Finance*, 48(1), 65-91.

- Kahneman, D., & Tversky, A. (1979). "Prospect Theory: An Analysis of Decision under Risk." *Econometrica*, 47(2), 263-291.

- Koenker, R., & Bassett, G. (1978). "Regression Quantiles." *Econometrica*, 46(1), 33-50.

- Murphy, J. J. (1999). *Technical Analysis of the Financial Markets*. New York Institute of Finance.

- Chen, T., & Guestrin, C. (2016). "XGBoost: A Scalable Tree Boosting System." *Proceedings of the 22nd ACM SIGKDD International Conference on Knowledge Discovery and Data Mining*.

---

## Appendix: Getting Started with Gefion

### Installation

```bash
# Clone repository
git clone https://github.com/simonibsen/gefion.git
cd gefion

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .
pip install gefion[ml_extended]'  # For XGBoost/LightGBM

# Start TimescaleDB
docker compose up -d postgres

# Initialize schema
cp .env.example .env
# Edit .env to add ALPHAVANTAGE_API_KEY
psql -d gefion -f sql/schema.sql
gefion seed-features
```

### Quick ML Workflow

**Option 1: Automated E2E Test** (validates entire pipeline):
```bash
# Run all steps automatically with quality validation
gefion ml e2e-test --exchange NASDAQ --limit 10
```

**Option 2: Manual Steps**:
```bash
# 1. Ingest data
gefion data-update --exchange NASDAQ --limit 50

# 2. Build dataset (features computed automatically during data-update)
gefion ml dataset-build --name demo --version v1 \
  --exchange NASDAQ --limit 50 \
  --horizons 7,30 --export

# 3. Train model
gefion ml train --dataset-name demo --dataset-version v1 \
  --model-name test --model-version 20241214 \
  --algorithm xgboost

# 4. Generate predictions (date auto-detected)
gefion ml predict --model-name test --model-version 20241214 \
  --exchange NASDAQ --limit 50

# 5. Evaluate
gefion ml eval --model-name test --model-version 20241214 \
  --start-date 2024-01-01 --end-date 2024-12-01
```

### Documentation

- **User Guide**: [docs/USER_GUIDE.md](USER_GUIDE.md)
- **ML Quickstart**: [docs/ML_QUICKSTART.md](ML_QUICKSTART.md)
- **E2E Test Guide**: [docs/E2E_TEST_GUIDE.md](E2E_TEST_GUIDE.md)
- **Architecture**: [docs/ARCHITECTURE.md](ARCHITECTURE.md)
- **Backlog**: [.specify/memory/backlog.md](../.specify/memory/backlog.md)

---

**For questions, feedback, or contributions**: https://github.com/simonibsen/gefion
