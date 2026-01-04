"""Backtesting page - Test trading strategies."""

import streamlit as st
import subprocess
import sys
import json
import os
from datetime import date, timedelta
import pandas as pd


def _parse_last_json(output: str) -> dict:
    """Parse the last JSON object from CLI output.

    The CLI outputs multiple JSON objects (progress messages followed by results).
    This function finds and parses the last complete JSON object.
    """
    # Try parsing the whole output first
    try:
        return json.loads(output)
    except json.JSONDecodeError:
        pass

    # Find all JSON objects by looking for opening braces at start of line
    lines = output.strip().split('\n')
    json_starts = []

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('{'):
            json_starts.append(i)

    # Try parsing from each JSON start, from last to first
    for start_idx in reversed(json_starts):
        try:
            remaining = '\n'.join(lines[start_idx:])
            return json.loads(remaining)
        except json.JSONDecodeError:
            continue

    # Fallback: try to find the last complete JSON object
    # by finding matching braces
    brace_count = 0
    last_start = -1

    for i, char in enumerate(output):
        if char == '{':
            if brace_count == 0:
                last_start = i
            brace_count += 1
        elif char == '}':
            brace_count -= 1
            if brace_count == 0 and last_start >= 0:
                try:
                    return json.loads(output[last_start:i+1])
                except json.JSONDecodeError:
                    last_start = -1

    raise json.JSONDecodeError("No valid JSON found", output, 0)


def _render_comparison_results(data: dict) -> None:
    """Render strategy comparison results with charts and tables."""
    comparison = data.get("comparison", {})
    equity_curves = data.get("equity_curves", {})
    benchmark = data.get("benchmark", {})
    ranking = data.get("ranking", [])

    if not comparison:
        st.warning("No comparison data found")
        return

    # How to read guide
    with st.expander("📖 How to Read This Comparison", expanded=False):
        st.markdown("""
        **Comparing Strategies:**
        - The table shows key metrics for each strategy side-by-side
        - Strategies are ranked by the selected metric (default: Sharpe ratio)
        - The equity curve chart shows how each strategy performed over time
        - The benchmark (buy & hold) helps you see if active trading added value

        **What to Look For:**
        - **Consistency**: Does the strategy beat the benchmark reliably?
        - **Risk-adjusted returns**: Higher Sharpe/Sortino = better return per unit of risk
        - **Drawdowns**: Smaller max drawdown = less painful to hold
        - **Trade count**: More trades = more transaction costs in real trading
        """)

    # Ranking summary
    if ranking:
        st.markdown("### 🏆 Ranking")
        rank_cols = st.columns(len(ranking))
        for i, rank_info in enumerate(ranking):
            with rank_cols[i]:
                strategy = rank_info.get("strategy", "")
                # Get the ranking metric value
                rank_value = list(rank_info.values())[1] if len(rank_info) > 1 else 0
                medal = "🥇" if i == 0 else ("🥈" if i == 1 else ("🥉" if i == 2 else f"#{i+1}"))
                st.metric(
                    f"{medal} {strategy}",
                    f"{rank_value:.2f}",
                    help=f"Ranked #{i+1}"
                )

    # Metrics comparison table
    st.markdown("### 📊 Performance Metrics")

    # Build comparison dataframe
    rows = []
    for strategy_name, metrics in comparison.items():
        rows.append({
            "Strategy": strategy_name,
            "Return %": f"{metrics.get('total_return', 0) * 100:.1f}%",
            "Sharpe": f"{metrics.get('sharpe_ratio', 0):.2f}",
            "Sortino": f"{metrics.get('sortino_ratio', 0):.2f}",
            "Calmar": f"{metrics.get('calmar_ratio', 0):.2f}",
            "Max DD": f"{metrics.get('max_drawdown', 0) * 100:.1f}%",
            "Win Rate": f"{metrics.get('win_rate', 0) * 100:.0f}%",
            "Profit Factor": f"{metrics.get('profit_factor', 0):.2f}",
            "Trades": metrics.get('total_trades', 0),
        })

    # Add benchmark row
    if benchmark:
        rows.append({
            "Strategy": f"📈 {benchmark.get('name', 'Benchmark')}",
            "Return %": f"{benchmark.get('total_return_pct', 0):.1f}%",
            "Sharpe": "-",
            "Sortino": "-",
            "Calmar": "-",
            "Max DD": "-",
            "Win Rate": "-",
            "Profit Factor": "-",
            "Trades": 0,
        })

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Equity curves chart
    if equity_curves:
        st.markdown("### 📈 Equity Curves")
        st.caption("Compare how each strategy's portfolio value changed over time. Higher = better.")

        # Build combined dataframe for charting
        combined_df = None

        for strategy_name, curve in equity_curves.items():
            if curve:
                curve_df = pd.DataFrame(curve)
                curve_df['date'] = pd.to_datetime(curve_df['date'])
                curve_df = curve_df.set_index('date')
                curve_df = curve_df.rename(columns={'equity': strategy_name})

                if combined_df is None:
                    combined_df = curve_df
                else:
                    combined_df = combined_df.join(curve_df, how='outer')

        # Add benchmark
        benchmark_curve = benchmark.get("equity_curve", [])
        if benchmark_curve and combined_df is not None:
            bench_df = pd.DataFrame(benchmark_curve)
            bench_df['date'] = pd.to_datetime(bench_df['date'])
            bench_df = bench_df.set_index('date')
            bench_df = bench_df.rename(columns={'equity': 'Benchmark'})
            combined_df = combined_df.join(bench_df, how='outer')

        if combined_df is not None:
            combined_df = combined_df.ffill()
            st.line_chart(combined_df, use_container_width=True)

            # Show alpha for each strategy vs benchmark
            if benchmark_curve:
                benchmark_return = benchmark.get('total_return_pct', 0)
                st.markdown("**Alpha vs Benchmark:**")
                alpha_cols = st.columns(len(comparison))
                for i, (strategy_name, metrics) in enumerate(comparison.items()):
                    with alpha_cols[i]:
                        strategy_return = metrics.get('total_return', 0) * 100
                        alpha = strategy_return - benchmark_return
                        color = "green" if alpha > 0 else "red"
                        st.markdown(f"**{strategy_name}**: :{color}[{alpha:+.1f}%]")


def _render_backtest_results(data: dict) -> None:
    """Render comprehensive backtest results with charts and tables."""
    metrics = data.get("metrics", {})

    # Primary metrics row
    st.markdown("### Performance Summary")

    # Quick interpretation guide
    with st.expander("📖 How to Read These Results", expanded=False):
        st.markdown("""
        **Key Metrics Explained:**

        | Metric | What It Measures | Good | Excellent |
        |--------|------------------|------|-----------|
        | **Total Return** | Profit/loss as % of initial capital | > 0% | > 20%/yr |
        | **Sharpe Ratio** | Risk-adjusted return (return per unit of volatility) | > 1.0 | > 2.0 |
        | **Max Drawdown** | Largest peak-to-trough decline | > -20% | > -10% |
        | **Win Rate** | % of trades that were profitable | > 50% | > 60% |
        | **Sortino Ratio** | Like Sharpe, but only penalizes downside moves | > 1.0 | > 2.0 |
        | **Calmar Ratio** | Annual return ÷ max drawdown (reward/risk) | > 1.0 | > 3.0 |
        | **Profit Factor** | Gross profits ÷ gross losses | > 1.5 | > 2.0 |
        | **Avg Win/Loss** | Average winning trade ÷ average losing trade | > 1.0 | > 1.5 |

        **Reading the Charts:**
        - **Equity Curve**: Shows portfolio value over time. Upward slope = gains, flat = no change, downward = losses
        - **Benchmark**: The dashed line shows buy-and-hold performance. If strategy is above, you're beating passive investing
        - **Alpha**: Strategy return minus benchmark return. Positive alpha = strategy adds value
        - **Drawdown**: Shows how far below the peak the portfolio fell. Deeper valleys = more painful periods

        **Red Flags to Watch:**
        - Max drawdown > 30% (hard to recover psychologically)
        - Sharpe < 0.5 (not enough return for the risk)
        - Win rate < 40% with avg win/loss < 1.5 (losing combination)
        - Very few trades (results may not be statistically significant)
        """)

    col1, col2, col3, col4 = st.columns(4)

    with col1:
        total_return = metrics.get('total_return_pct', 0)
        st.metric(
            "Total Return",
            f"{total_return:.1f}%",
            delta=f"{'↑' if total_return > 0 else '↓'}" if total_return != 0 else None,
            help="Total profit/loss as percentage of initial capital",
        )
    with col2:
        sharpe = metrics.get('sharpe_ratio', 0)
        # Color-code the delta based on value
        if sharpe >= 2.0:
            delta_text = "Excellent"
        elif sharpe >= 1.0:
            delta_text = "Good"
        elif sharpe >= 0.5:
            delta_text = "Fair"
        else:
            delta_text = "Poor" if sharpe > 0 else None
        st.metric(
            "Sharpe Ratio",
            f"{sharpe:.2f}",
            delta=delta_text,
            help="Risk-adjusted return. Higher = better return per unit of risk. >1 good, >2 excellent",
        )
    with col3:
        max_dd = metrics.get('max_drawdown_pct', 0)
        st.metric(
            "Max Drawdown",
            f"{max_dd:.1f}%",
            help="Largest peak-to-trough decline. This is the worst loss you would have experienced",
        )
    with col4:
        win_rate = metrics.get('win_rate', 0)
        st.metric(
            "Win Rate",
            f"{win_rate*100:.0f}%",
            help="Percentage of trades that were profitable. Note: low win rate can still be profitable with high avg win/loss",
        )

    # Extended metrics row
    col5, col6, col7, col8 = st.columns(4)

    with col5:
        sortino = metrics.get('sortino_ratio', 0)
        st.metric(
            "Sortino Ratio",
            f"{sortino:.2f}",
            help="Like Sharpe but only penalizes downside volatility. Better for asymmetric strategies",
        )
    with col6:
        calmar = metrics.get('calmar_ratio', 0)
        st.metric(
            "Calmar Ratio",
            f"{calmar:.2f}",
            help="Annualized return ÷ max drawdown. Measures reward relative to worst-case risk",
        )
    with col7:
        profit_factor = metrics.get('profit_factor', 0)
        st.metric(
            "Profit Factor",
            f"{profit_factor:.2f}",
            help="Total profits ÷ total losses. >1 means profitable, >2 is strong",
        )
    with col8:
        avg_wl = metrics.get('avg_win_loss_ratio', 0)
        st.metric(
            "Avg Win/Loss",
            f"{avg_wl:.2f}",
            help="Average winning trade size ÷ average losing trade. >1 means wins are bigger than losses",
        )

    # Charts section
    equity_curve = data.get("equity_curve", [])
    drawdown_series = data.get("drawdown_series", [])
    benchmark = data.get("benchmark", {})

    if equity_curve:
        st.markdown("### Equity Curve")

        # Create equity dataframe
        equity_df = pd.DataFrame(equity_curve)
        if 'date' in equity_df.columns and 'equity' in equity_df.columns:
            equity_df['date'] = pd.to_datetime(equity_df['date'])
            equity_df = equity_df.set_index('date')
            equity_df = equity_df.rename(columns={'equity': 'Strategy'})

            # Add benchmark if available
            benchmark_curve = benchmark.get("equity_curve", [])
            if benchmark_curve:
                bench_df = pd.DataFrame(benchmark_curve)
                bench_df['date'] = pd.to_datetime(bench_df['date'])
                bench_df = bench_df.set_index('date')
                bench_df = bench_df.rename(columns={'equity': 'Benchmark'})

                # Merge strategy and benchmark
                combined_df = equity_df.join(bench_df, how='outer')
                combined_df = combined_df.ffill()  # Forward fill missing values

                st.line_chart(combined_df, use_container_width=True)

                # Show alpha (strategy - benchmark return)
                strategy_return = metrics.get('total_return_pct', 0)
                benchmark_return = benchmark.get('total_return_pct', 0)
                alpha = strategy_return - benchmark_return

                st.caption(
                    f"📊 **Strategy**: {strategy_return:.1f}% | "
                    f"**Benchmark** ({benchmark.get('name', 'Buy & Hold')}): {benchmark_return:.1f}% | "
                    f"**Alpha**: {alpha:+.1f}%"
                )
            else:
                st.line_chart(equity_df['Strategy'], use_container_width=True)

    if drawdown_series:
        st.markdown("### Drawdown")
        st.caption("📉 Shows how far below the peak your portfolio fell at each point. Deeper red = bigger loss from peak.")

        # Create drawdown dataframe
        dd_df = pd.DataFrame(drawdown_series)
        if 'date' in dd_df.columns and 'drawdown_pct' in dd_df.columns:
            dd_df['date'] = pd.to_datetime(dd_df['date'])
            dd_df = dd_df.set_index('date')

            # Display drawdown chart (negative values)
            st.area_chart(dd_df['drawdown_pct'], use_container_width=True, color="#ff6b6b")

            # Find worst drawdown period
            if not dd_df.empty:
                worst_idx = dd_df['drawdown_pct'].idxmin()
                worst_dd = dd_df.loc[worst_idx, 'drawdown_pct']
                st.caption(f"💡 Worst drawdown: **{worst_dd:.1f}%** on {worst_idx.strftime('%Y-%m-%d')}")

    # Monthly returns
    monthly_returns = data.get("monthly_returns", [])
    if monthly_returns:
        with st.expander("📅 Monthly Returns"):
            st.caption("Shows return for each month. Green = profit, Red = loss. Look for consistency across months.")
            monthly_df = pd.DataFrame(monthly_returns)
            if not monthly_df.empty:
                # Calculate summary stats
                returns = [m.get('return_pct', 0) for m in monthly_returns]
                positive_months = sum(1 for r in returns if r > 0)
                negative_months = sum(1 for r in returns if r < 0)
                best_month = max(returns) if returns else 0
                worst_month = min(returns) if returns else 0

                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Positive Months", positive_months)
                with col2:
                    st.metric("Negative Months", negative_months)
                with col3:
                    st.metric("Best Month", f"{best_month:+.1f}%")
                with col4:
                    st.metric("Worst Month", f"{worst_month:+.1f}%")

                # Color the returns
                st.dataframe(
                    monthly_df.style.applymap(
                        lambda x: 'color: green' if isinstance(x, (int, float)) and x > 0
                                  else ('color: red' if isinstance(x, (int, float)) and x < 0 else ''),
                        subset=['return_pct'] if 'return_pct' in monthly_df.columns else []
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

    # Trades table
    trades = data.get("trades", [])
    if trades:
        st.markdown("### Trades")
        st.caption("📋 Complete list of all buy/sell orders executed by the strategy.")

        # Summary
        total_trades = len(trades)
        buys = [t for t in trades if t.get('action') == 'buy']
        sells = [t for t in trades if t.get('action') == 'sell']

        # Calculate trade statistics
        profitable_sells = [t for t in sells if t.get('pnl', 0) > 0]
        losing_sells = [t for t in sells if t.get('pnl', 0) < 0]

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("Total Trades", total_trades, help="Total number of executed orders")
        with col2:
            st.metric("Buy Orders", len(buys), help="Number of positions opened")
        with col3:
            st.metric("Sell Orders", len(sells), help="Number of positions closed")
        with col4:
            if sells:
                sell_win_rate = len(profitable_sells) / len(sells) * 100
                st.metric("Sell Win Rate", f"{sell_win_rate:.0f}%", help="% of sells that were profitable")

        # Trades table
        trades_df = pd.DataFrame(trades)
        if not trades_df.empty:
            # Format columns for display
            display_cols = ['date', 'action', 'symbol', 'shares', 'price', 'pnl', 'reason']
            available_cols = [c for c in display_cols if c in trades_df.columns]

            if available_cols:
                display_df = trades_df[available_cols].copy()

                # Format price and pnl
                if 'price' in display_df.columns:
                    display_df['price'] = display_df['price'].apply(lambda x: f"${x:.2f}" if pd.notna(x) else "")
                if 'pnl' in display_df.columns:
                    display_df['pnl'] = display_df['pnl'].apply(
                        lambda x: f"${x:+,.2f}" if pd.notna(x) and x != 0 else ""
                    )

                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "date": st.column_config.DateColumn("Date"),
                        "action": st.column_config.TextColumn("Action"),
                        "symbol": st.column_config.TextColumn("Symbol"),
                        "shares": st.column_config.NumberColumn("Shares", format="%d"),
                        "price": st.column_config.TextColumn("Price"),
                        "pnl": st.column_config.TextColumn("P&L"),
                        "reason": st.column_config.TextColumn("Reason"),
                    }
                )


def render_backtest():
    """Render the backtesting page."""
    st.title("📈 Backtesting")
    st.markdown("Test trading strategies on historical data.")

    tab1, tab2, tab3 = st.tabs(["🎮 Run Backtest", "⚔️ Compare Strategies", "📊 Help"])

    with tab1:
        render_run_section()

    with tab2:
        render_compare_section()

    with tab3:
        render_help_section()


def get_strategies():
    """Get available strategies from database or dispatcher."""
    try:
        from g2.ui.components.database import get_connection
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT name, description
                    FROM strategy_registry
                    WHERE enabled = true
                    ORDER BY name
                """)
                results = [(row[0], row[1]) for row in cur.fetchall()]
                if results:
                    return results
    except Exception:
        pass

    # Fallback to dispatcher's built-in strategies
    try:
        from g2.strategies.dispatcher import BUILTIN_STRATEGIES
        return [
            (name, info["description"])
            for name, info in sorted(BUILTIN_STRATEGIES.items())
        ]
    except Exception:
        # Last resort fallback
        return [
            ("momentum", "Momentum-based strategy"),
            ("mean_reversion", "Mean reversion using RSI"),
            ("ma_crossover", "Moving average crossover"),
            ("breakout", "Breakout with volume confirmation"),
            ("pairs_trading", "Statistical arbitrage on correlated pairs"),
            ("rsi_divergence", "RSI divergence detection"),
            ("volatility_contraction", "Volatility squeeze and expansion"),
            ("ml_signal", "ML-based predictions strategy"),
            ("ml_filter", "Hybrid ML filter strategy"),
        ]


def render_run_section():
    """Render backtest execution section."""
    st.subheader("Run Backtest")

    col1, col2 = st.columns(2)

    strategies = get_strategies()
    strategy_names = [s[0] for s in strategies]
    strategy_descriptions = {s[0]: s[1] for s in strategies}

    with col1:
        strategy = st.selectbox(
            "Strategy",
            strategy_names,
            help=strategy_descriptions.get(strategy_names[0], "") if strategy_names else "",
            format_func=lambda x: f"{x} - {strategy_descriptions.get(x, '')}"[:50],
        )

        end_date = st.date_input(
            "End Date",
            value=date.today() - timedelta(days=1),
            key="bt_end",
        )
        start_date = st.date_input(
            "Start Date",
            value=end_date - timedelta(days=365),
            key="bt_start",
        )

        initial_cash = st.number_input(
            "Initial Capital ($)",
            min_value=1000,
            max_value=10000000,
            value=100000,
            step=10000,
        )

    with col2:
        from g2.ui.components.database import get_symbols
        symbols = get_symbols()

        symbol_mode = st.radio(
            "Symbol Selection",
            ["Selected", "Exchange"],
            horizontal=True,
        )

        if symbol_mode == "Selected":
            selected_symbols = st.multiselect(
                "Select Symbols",
                symbols,
                default=symbols[:5] if len(symbols) >= 5 else symbols,
            )
        else:
            exchange = st.selectbox(
                "Exchange",
                ["NASDAQ", "NYSE"],
                key="bt_exchange",
            )
            bt_limit = st.number_input(
                "Limit",
                min_value=5,
                max_value=100,
                value=20,
                key="bt_limit",
            )

    # Strategy-specific parameters
    st.markdown("##### Strategy Parameters")

    if strategy == "momentum":
        col1, col2, col3 = st.columns(3)
        with col1:
            lookback = st.number_input("Lookback Days", value=20, min_value=5, max_value=60)
        with col2:
            top_n = st.number_input("Top N Stocks", value=10, min_value=1, max_value=50)
        with col3:
            rebalance = st.number_input("Rebalance Days", value=5, min_value=1, max_value=30)

    elif strategy == "mean_reversion":
        col1, col2, col3 = st.columns(3)
        with col1:
            rsi_oversold = st.number_input("RSI Oversold", value=30, min_value=10, max_value=40)
        with col2:
            rsi_overbought = st.number_input("RSI Overbought", value=70, min_value=60, max_value=90)
        with col3:
            rsi_period = st.number_input("RSI Period", value=14, min_value=5, max_value=30, key="mr_rsi_period")
        col4, col5 = st.columns(2)
        with col4:
            position_size = st.slider("Position Size", value=0.2, min_value=0.05, max_value=0.5)
        with col5:
            max_positions = st.number_input("Max Positions", value=5, min_value=1, max_value=20, key="mr_max_pos")

    elif strategy == "ma_crossover":
        col1, col2, col3 = st.columns(3)
        with col1:
            fast_period = st.number_input("Fast MA Period", value=50, min_value=5, max_value=100)
        with col2:
            slow_period = st.number_input("Slow MA Period", value=200, min_value=50, max_value=300)
        with col3:
            max_positions = st.number_input("Max Positions", value=5, min_value=1, max_value=20, key="mac_max_pos")

    elif strategy == "breakout":
        col1, col2 = st.columns(2)
        with col1:
            lookback = st.number_input("Lookback Days", value=20, min_value=5, max_value=60, key="bo_lookback")
        with col2:
            volume_threshold = st.slider("Volume Threshold", value=1.5, min_value=1.0, max_value=3.0)

    elif strategy == "pairs_trading":
        col1, col2 = st.columns(2)
        with col1:
            entry_zscore = st.number_input("Entry Z-Score", value=2.0, min_value=1.0, max_value=4.0, step=0.1)
        with col2:
            exit_zscore = st.number_input("Exit Z-Score", value=0.5, min_value=0.0, max_value=2.0, step=0.1)

    elif strategy == "rsi_divergence":
        col1, col2 = st.columns(2)
        with col1:
            rsi_period = st.number_input("RSI Period", value=14, min_value=5, max_value=30)
        with col2:
            divergence_lookback = st.number_input("Divergence Lookback", value=10, min_value=3, max_value=30)

    elif strategy == "volatility_contraction":
        col1, col2 = st.columns(2)
        with col1:
            bb_period = st.number_input("Bollinger Period", value=20, min_value=10, max_value=50)
        with col2:
            bb_std_dev = st.number_input("Std Dev Multiplier", value=2.0, min_value=1.0, max_value=3.0, step=0.1)
        col3, col4 = st.columns(2)
        with col3:
            squeeze_threshold = st.slider("Squeeze Threshold", value=0.05, min_value=0.01, max_value=0.15, step=0.01)
        with col4:
            expansion_threshold = st.slider("Expansion Threshold", value=0.1, min_value=0.05, max_value=0.25, step=0.01)

    elif strategy == "ml_signal":
        st.info("ML Signal Strategy uses predictions from trained ML models to generate trading signals.")

        # Get available models from database
        from g2.ui.components.database import get_models
        available_models = get_models()

        if not available_models:
            st.warning("No ML models found. Train a model first using the ML Pipeline page.")
            ml_model_name = st.text_input("Model Name", value="quantile")
            ml_model_version = st.text_input("Model Version", value="latest")
        else:
            model_options = [f"{m['name']} / {m['version']}" for m in available_models]
            selected_model = st.selectbox("Select Model", model_options)
            if selected_model:
                parts = selected_model.split(" / ")
                ml_model_name = parts[0]
                ml_model_version = parts[1] if len(parts) > 1 else "latest"
            else:
                ml_model_name = "quantile"
                ml_model_version = "latest"

        col1, col2 = st.columns(2)
        with col1:
            ml_horizon = st.selectbox("Prediction Horizon", [7, 30, 90], index=0)
            ml_prediction_type = st.selectbox("Prediction Type", ["quantile", "classifier"])
        with col2:
            ml_return_threshold = st.number_input(
                "Return Threshold",
                value=0.02,
                min_value=0.0,
                max_value=0.20,
                step=0.01,
                help="Min expected return (q50) to buy"
            )
            ml_max_positions = st.number_input("Max Positions", value=10, min_value=1, max_value=50, key="ml_max_pos")

        if ml_prediction_type == "classifier":
            ml_trend_classes = st.multiselect(
                "Trend Classes (buy signals)",
                ["strong_up", "weak_up", "neutral", "weak_down", "strong_down"],
                default=["strong_up", "weak_up"]
            )
            ml_confidence = st.slider("Confidence Threshold", value=0.5, min_value=0.3, max_value=0.9, step=0.05)
        else:
            ml_downside_limit = st.number_input(
                "Downside Limit (q10)",
                value=-0.05,
                min_value=-0.20,
                max_value=0.0,
                step=0.01,
                help="Max acceptable downside risk"
            )

    elif strategy == "ml_filter":
        st.info("ML Filter wraps a base strategy and filters its buy signals through ML predictions.")

        # Base strategy selection (exclude ml_signal and ml_filter)
        base_strategies = ["momentum", "mean_reversion", "ma_crossover", "breakout"]
        base_strategy = st.selectbox("Base Strategy", base_strategies, key="filter_base")

        # Base strategy parameters
        st.markdown("**Base Strategy Parameters**")
        if base_strategy == "momentum":
            col1, col2 = st.columns(2)
            with col1:
                base_lookback = st.number_input("Lookback Days", value=20, min_value=5, max_value=60, key="fb_lookback")
            with col2:
                base_top_n = st.number_input("Top N Stocks", value=5, min_value=1, max_value=20, key="fb_topn")
        elif base_strategy == "mean_reversion":
            col1, col2 = st.columns(2)
            with col1:
                base_rsi_oversold = st.number_input("RSI Oversold", value=30, min_value=10, max_value=40, key="fb_oversold")
            with col2:
                base_rsi_overbought = st.number_input("RSI Overbought", value=70, min_value=60, max_value=90, key="fb_overbought")
        elif base_strategy == "ma_crossover":
            col1, col2 = st.columns(2)
            with col1:
                base_fast = st.number_input("Fast MA", value=50, min_value=5, max_value=100, key="fb_fast")
            with col2:
                base_slow = st.number_input("Slow MA", value=200, min_value=50, max_value=300, key="fb_slow")
        elif base_strategy == "breakout":
            col1, col2 = st.columns(2)
            with col1:
                base_lookback = st.number_input("Lookback Days", value=20, min_value=5, max_value=60, key="fb_bo_lookback")
            with col2:
                base_volume = st.slider("Volume Threshold", value=1.5, min_value=1.0, max_value=3.0, key="fb_volume")

        st.markdown("---")
        st.markdown("**ML Filter Settings**")

        # ML model selection
        from g2.ui.components.database import get_models
        available_models = get_models()

        if not available_models:
            st.warning("No ML models found. Train a model first.")
            filter_model_name = st.text_input("Model Name", value="quantile", key="fm_name")
            filter_model_version = st.text_input("Model Version", value="latest", key="fm_version")
        else:
            model_options = [f"{m['name']} / {m['version']}" for m in available_models]
            selected_model = st.selectbox("Select Model", model_options, key="fm_select")
            if selected_model:
                parts = selected_model.split(" / ")
                filter_model_name = parts[0]
                filter_model_version = parts[1] if len(parts) > 1 else "latest"
            else:
                filter_model_name = "quantile"
                filter_model_version = "latest"

        col1, col2 = st.columns(2)
        with col1:
            filter_horizon = st.selectbox("Prediction Horizon", [7, 30, 90], index=0, key="fm_horizon")
            filter_mode = st.selectbox(
                "Filter Mode",
                ["confirm", "veto"],
                help="confirm: require positive ML outlook. veto: only block strongly negative."
            )
        with col2:
            filter_min_q50 = st.number_input(
                "Min q50",
                value=0.0,
                min_value=-0.10,
                max_value=0.20,
                step=0.01,
                help="Minimum expected return to pass filter"
            )
            filter_max_q10 = st.number_input(
                "Max q10 (downside limit)",
                value=-0.10,
                min_value=-0.30,
                max_value=0.0,
                step=0.01,
                help="Block if q10 below this"
            )

    # Validate symbols selection
    if symbol_mode == "Selected" and not selected_symbols:
        st.warning("⚠️ Please select at least one symbol to backtest.")

    if st.button("🚀 Run Backtest", type="primary", use_container_width=True):
        # Validate before running
        if symbol_mode == "Selected" and not selected_symbols:
            st.error("No symbols selected. Please select at least one symbol.")
            st.stop()

        # Build command
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        cmd = [
            sys.executable, "-m", "g2.cli", "backtest", "run",
            "--strategy", strategy,
            "--start-date", str(start_date),
            "--end-date", str(end_date),
            "--initial-cash", str(initial_cash),
            "--json",
        ]

        # Add strategy-specific options
        if strategy == "momentum":
            cmd.extend([
                "--lookback-days", str(lookback),
                "--top-n", str(top_n),
                "--rebalance-days", str(rebalance),
            ])
        elif strategy == "mean_reversion":
            cmd.extend([
                "--rsi-oversold", str(rsi_oversold),
                "--rsi-overbought", str(rsi_overbought),
                "--rsi-period", str(rsi_period),
                "--position-size", str(position_size),
                "--max-positions", str(max_positions),
            ])
        elif strategy == "ma_crossover":
            cmd.extend([
                "--fast-period", str(fast_period),
                "--slow-period", str(slow_period),
                "--max-positions", str(max_positions),
            ])
        elif strategy == "breakout":
            cmd.extend([
                "--lookback-days", str(lookback),
                "--volume-threshold", str(volume_threshold),
            ])
        elif strategy == "pairs_trading":
            cmd.extend([
                "--entry-zscore", str(entry_zscore),
                "--exit-zscore", str(exit_zscore),
            ])
        elif strategy == "rsi_divergence":
            cmd.extend([
                "--rsi-period", str(rsi_period),
                "--divergence-lookback", str(divergence_lookback),
            ])
        elif strategy == "volatility_contraction":
            cmd.extend([
                "--bb-period", str(bb_period),
                "--bb-std-dev", str(bb_std_dev),
                "--squeeze-threshold", str(squeeze_threshold),
                "--expansion-threshold", str(expansion_threshold),
            ])
        elif strategy == "ml_signal":
            cmd.extend([
                "--model-name", ml_model_name,
                "--model-version", ml_model_version,
                "--horizon-days", str(ml_horizon),
                "--prediction-type", ml_prediction_type,
                "--return-threshold", str(ml_return_threshold),
                "--max-positions", str(ml_max_positions),
            ])
            if ml_prediction_type == "classifier":
                cmd.extend([
                    "--trend-classes", ",".join(ml_trend_classes),
                    "--confidence-threshold", str(ml_confidence),
                ])
            else:
                cmd.extend([
                    "--downside-limit", str(ml_downside_limit),
                ])
        elif strategy == "ml_filter":
            cmd.extend([
                "--base-strategy", base_strategy,
                "--model-name", filter_model_name,
                "--model-version", filter_model_version,
                "--horizon-days", str(filter_horizon),
                "--filter-mode", filter_mode,
                "--filter-min-q50", str(filter_min_q50),
                "--filter-max-q10", str(filter_max_q10),
            ])
            # Add base strategy parameters
            if base_strategy == "momentum":
                cmd.extend([
                    "--lookback-days", str(base_lookback),
                    "--top-n", str(base_top_n),
                ])
            elif base_strategy == "mean_reversion":
                cmd.extend([
                    "--rsi-oversold", str(base_rsi_oversold),
                    "--rsi-overbought", str(base_rsi_overbought),
                ])
            elif base_strategy == "ma_crossover":
                cmd.extend([
                    "--fast-period", str(base_fast),
                    "--slow-period", str(base_slow),
                ])
            elif base_strategy == "breakout":
                cmd.extend([
                    "--lookback-days", str(base_lookback),
                    "--volume-threshold", str(base_volume),
                ])

        if symbol_mode == "Selected":
            cmd.extend(["--symbols", ",".join(selected_symbols)])
        else:
            cmd.extend(["--exchange", exchange, "--limit", str(bt_limit)])

        # Show equivalent CLI command (skip "python -m g2.cli" prefix)
        cli_args = cmd[3:]  # Skip [python, -m, g2.cli]
        st.code(f"g2 {' '.join(cli_args)}", language="bash")

        with st.status("Running backtest...", expanded=True) as status:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=600,
                )

                if result.returncode == 0:
                    status.update(label="✅ Backtest complete!", state="complete")

                    try:
                        data = _parse_last_json(result.stdout)
                        _render_backtest_results(data)
                    except Exception as e:
                        st.warning(f"Could not parse results: {e}")

                    with st.expander("Raw JSON Output"):
                        st.code(result.stdout)
                else:
                    status.update(label="❌ Backtest failed", state="error")
                    st.error("Backtest failed")
                    st.code(result.stderr)

            except subprocess.TimeoutExpired:
                status.update(label="❌ Timeout", state="error")
                st.error("Backtest timed out after 10 minutes")
            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")


def render_compare_section():
    """Render strategy comparison section."""
    st.subheader("Compare Strategies")

    col1, col2 = st.columns(2)

    # Load strategies from database
    available_strategies = get_strategies()
    strategy_names = [s[0] for s in available_strategies]

    with col1:
        strategies = st.multiselect(
            "Strategies to Compare",
            strategy_names,
            default=strategy_names[:2] if len(strategy_names) >= 2 else strategy_names,
        )

        end_date = st.date_input(
            "End Date",
            value=date.today() - timedelta(days=1),
            key="cmp_end",
        )
        start_date = st.date_input(
            "Start Date",
            value=end_date - timedelta(days=365),
            key="cmp_start",
        )

    with col2:
        from g2.ui.components.database import get_symbols
        symbols = get_symbols()

        symbol_mode = st.radio(
            "Symbol Selection",
            ["Selected", "Exchange"],
            horizontal=True,
            key="cmp_symbol_mode",
        )

        if symbol_mode == "Selected":
            selected_symbols = st.multiselect(
                "Select Symbols",
                symbols,
                default=symbols[:10] if len(symbols) >= 10 else symbols,
                key="cmp_symbols",
            )
        else:
            cmp_exchange = st.selectbox(
                "Exchange",
                ["NASDAQ", "NYSE"],
                key="cmp_exchange",
            )
            cmp_limit = st.number_input(
                "Limit",
                min_value=5,
                max_value=100,
                value=20,
                key="cmp_limit",
            )

    if len(strategies) < 2:
        st.warning("Select at least 2 strategies to compare.")
        return

    # ML strategy configuration (if ml_signal or ml_filter selected)
    ml_strategies = [s for s in strategies if s in ("ml_signal", "ml_filter")]
    if ml_strategies:
        st.markdown("---")
        st.markdown("### 🤖 ML Strategy Settings")
        st.caption("Configure ML model settings for ML-based strategies")

        from g2.ui.components.database import get_models
        available_models = get_models()

        if not available_models:
            st.warning("No ML models found. Train a model first using the ML Pipeline page.")
            cmp_model_name = st.text_input("Model Name", value="quantile", key="cmp_ml_name")
            cmp_model_version = st.text_input("Model Version", value="latest", key="cmp_ml_version")
        else:
            model_options = [f"{m['name']} / {m['version']}" for m in available_models]
            selected_model = st.selectbox("Select Model", model_options, key="cmp_ml_select")
            if selected_model:
                parts = selected_model.split(" / ")
                cmp_model_name = parts[0]
                cmp_model_version = parts[1] if len(parts) > 1 else "latest"
            else:
                cmp_model_name = "quantile"
                cmp_model_version = "latest"

        col_ml1, col_ml2 = st.columns(2)
        with col_ml1:
            cmp_ml_horizon = st.selectbox("Prediction Horizon (days)", [7, 30, 90], index=0, key="cmp_ml_horizon")
        with col_ml2:
            cmp_ml_threshold = st.slider("Confidence Threshold", 0.0, 0.5, 0.1, 0.05, key="cmp_ml_thresh")

        st.markdown("---")

    # Validate symbol selection
    if symbol_mode == "Selected" and not selected_symbols:
        st.warning("⚠️ Please select at least one symbol to compare.")

    if st.button("⚔️ Compare", type="primary", use_container_width=True):
        # Validate before running
        if symbol_mode == "Selected" and not selected_symbols:
            st.error("No symbols selected. Please select at least one symbol.")
            st.stop()

        # Build command
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        cmd = [
            sys.executable, "-m", "g2.cli", "backtest", "compare",
            "--strategies", ",".join(strategies),
            "--start-date", str(start_date),
            "--end-date", str(end_date),
            "--json",
        ]

        if symbol_mode == "Selected":
            cmd.extend(["--symbols", ",".join(selected_symbols)])
        else:
            cmd.extend(["--exchange", cmp_exchange, "--limit", str(cmp_limit)])

        # Add ML parameters if ML strategies selected
        if ml_strategies:
            cmd.extend([
                "--model-name", cmp_model_name,
                "--model-version", cmp_model_version,
                "--horizon-days", str(cmp_ml_horizon),
            ])

        # Show equivalent CLI command (skip "python -m g2.cli" prefix)
        cli_args = cmd[3:]  # Skip [python, -m, g2.cli]
        st.code(f"g2 {' '.join(cli_args)}", language="bash")

        with st.status("Comparing strategies...", expanded=True) as status:
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=600,
                )

                if result.returncode == 0:
                    status.update(label="✅ Comparison complete!", state="complete")

                    try:
                        data = _parse_last_json(result.stdout)
                        _render_comparison_results(data)
                    except Exception as e:
                        st.warning(f"Could not parse results: {e}")

                    with st.expander("Raw JSON Output"):
                        st.code(result.stdout)
                else:
                    status.update(label="❌ Comparison failed", state="error")
                    st.error("Comparison failed")
                    st.code(result.stderr)

            except subprocess.TimeoutExpired:
                status.update(label="❌ Timeout", state="error")
                st.error("Comparison timed out after 10 minutes")
            except Exception as e:
                status.update(label="❌ Error", state="error")
                st.error(f"Error: {e}")


def render_help_section():
    """Render help and documentation."""
    st.subheader("Strategy Guide")

    st.markdown("""
    ### Rule-Based Strategies

    #### Momentum
    Buys top performing stocks over the lookback period.
    - **Lookback**: Days to measure momentum (default: 20)
    - **Top N**: Number of stocks to hold (default: 10)
    - **Rebalance**: Days between portfolio rebalancing (default: 5)

    #### Mean Reversion
    Buys oversold stocks expecting bounce back.
    - **RSI Oversold**: Buy when RSI below this (default: 30)
    - **RSI Overbought**: Sell when RSI above this (default: 70)
    - **Position Size**: Fraction of portfolio per trade (default: 0.2)

    #### MA Crossover
    Follows moving average crossover signals.
    - **Fast Period**: Fast MA period in days (default: 50)
    - **Slow Period**: Slow MA period in days (default: 200)
    - Buy on golden cross, sell on death cross

    #### Breakout
    Buys when price breaks above recent highs.
    - **Lookback**: Days for range calculation (default: 20)
    - **Volume Threshold**: Volume multiplier for confirmation (default: 1.5)

    ---

    ### ML-Integrated Strategies

    #### ML Signal
    Pure ML-driven strategy using stored predictions.
    - **Model**: Select trained quantile or classifier model
    - **Horizon**: Prediction horizon (7, 30, or 90 days)
    - **Return Threshold**: Min expected return (q50) to buy
    - Uses **D-1 predictions** to avoid look-ahead bias

    #### ML Filter
    Wraps a base strategy and filters signals through ML predictions.
    - **Base Strategy**: Rule-based strategy to use (momentum, mean_reversion, etc.)
    - **Filter Mode**: 'confirm' requires positive outlook, 'veto' only blocks strongly negative
    - **Min q50**: Minimum expected return to pass filter
    - **Max q10**: Block trades with downside risk below this
    - Uses **D-1 predictions** to avoid look-ahead bias
    """)

    st.markdown("---")
    st.markdown("""
    ### Interpreting Results

    | Metric | Good | Excellent |
    |--------|------|-----------|
    | Sharpe Ratio | > 1.0 | > 2.0 |
    | Max Drawdown | < 20% | < 10% |
    | Win Rate | > 50% | > 60% |
    """)
