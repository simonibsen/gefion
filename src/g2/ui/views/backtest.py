"""Backtesting page - Test trading strategies."""

import streamlit as st
import subprocess
import sys
import json
import os
from datetime import date, timedelta


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
    """Get available strategies from database."""
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
                return [(row[0], row[1]) for row in cur.fetchall()]
    except Exception:
        # Fallback to built-in list
        return [
            ("momentum", "Momentum-based strategy"),
            ("mean_reversion", "Mean reversion using RSI"),
            ("ma_crossover", "Moving average crossover"),
            ("breakout", "Breakout with volume confirmation"),
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
            position_size = st.slider("Position Size", value=0.2, min_value=0.05, max_value=0.5)

    elif strategy == "ma_crossover":
        col1, col2 = st.columns(2)
        with col1:
            fast_period = st.number_input("Fast MA Period", value=50, min_value=5, max_value=100)
        with col2:
            slow_period = st.number_input("Slow MA Period", value=200, min_value=50, max_value=300)

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
        col1, col2, col3 = st.columns(3)
        with col1:
            bb_period = st.number_input("Bollinger Period", value=20, min_value=10, max_value=50)
        with col2:
            bb_std_dev = st.number_input("Std Dev Multiplier", value=2.0, min_value=1.0, max_value=3.0, step=0.1)
        with col3:
            squeeze_threshold = st.slider("Squeeze Threshold", value=0.05, min_value=0.01, max_value=0.15, step=0.01)

    if st.button("🚀 Run Backtest", type="primary", use_container_width=True):
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
                "--position-size", str(position_size),
            ])
        elif strategy == "ma_crossover":
            cmd.extend([
                "--fast-period", str(fast_period),
                "--slow-period", str(slow_period),
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
            ])

        if symbol_mode == "Selected":
            cmd.extend(["--symbols", ",".join(selected_symbols)])
        else:
            cmd.extend(["--exchange", exchange, "--limit", str(bt_limit)])

        # Show equivalent CLI command
        cli_cmd = " ".join(cmd[2:])  # Skip python -m prefix
        st.code(f"g2 {cli_cmd[8:]}", language="bash")  # Skip "g2.cli " prefix

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
                        data = json.loads(result.stdout)

                        if "metrics" in data:
                            metrics = data["metrics"]

                            col1, col2, col3, col4 = st.columns(4)

                            with col1:
                                st.metric(
                                    "Total Return",
                                    f"{metrics.get('total_return_pct', 0):.1f}%",
                                )
                            with col2:
                                st.metric(
                                    "Sharpe Ratio",
                                    f"{metrics.get('sharpe_ratio', 0):.2f}",
                                )
                            with col3:
                                st.metric(
                                    "Max Drawdown",
                                    f"{metrics.get('max_drawdown_pct', 0):.1f}%",
                                )
                            with col4:
                                st.metric(
                                    "Win Rate",
                                    f"{metrics.get('win_rate', 0)*100:.0f}%",
                                )
                    except Exception:
                        pass

                    with st.expander("Full Output"):
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

        selected_symbols = st.multiselect(
            "Symbols",
            symbols,
            default=symbols[:10] if len(symbols) >= 10 else symbols,
            key="cmp_symbols",
        )

    if len(strategies) < 2:
        st.warning("Select at least 2 strategies to compare.")
        return

    if st.button("⚔️ Compare", type="primary", use_container_width=True):
        # Build command
        env = os.environ.copy()
        env["OTEL_ENABLED"] = "false"

        cmd = [
            sys.executable, "-m", "g2.cli", "backtest", "compare",
            "--strategies", ",".join(strategies),
            "--start-date", str(start_date),
            "--end-date", str(end_date),
            "--symbols", ",".join(selected_symbols),
            "--json",
        ]

        # Show equivalent CLI command
        cli_cmd = " ".join(cmd[2:])  # Skip python -m prefix
        st.code(f"g2 {cli_cmd[8:]}", language="bash")  # Skip "g2.cli " prefix

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
                        data = json.loads(result.stdout)

                        if "results" in data:
                            import pandas as pd
                            df = pd.DataFrame(data["results"])
                            st.dataframe(df, use_container_width=True)
                    except Exception:
                        pass

                    with st.expander("Full Output"):
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
    ### Momentum
    Buys top performing stocks over the lookback period.
    - **Lookback**: Days to measure momentum (default: 20)
    - **Top N**: Number of stocks to hold (default: 10)
    - **Rebalance**: Days between portfolio rebalancing (default: 5)

    ### Mean Reversion
    Buys oversold stocks expecting bounce back.
    - **RSI Oversold**: Buy when RSI below this (default: 30)
    - **RSI Overbought**: Sell when RSI above this (default: 70)
    - **Position Size**: Fraction of portfolio per trade (default: 0.2)

    ### MA Crossover
    Follows moving average crossover signals.
    - **Fast Period**: Fast MA period in days (default: 50)
    - **Slow Period**: Slow MA period in days (default: 200)
    - Buy on golden cross, sell on death cross

    ### Breakout
    Buys when price breaks above recent highs.
    - **Lookback**: Days for range calculation (default: 20)
    - **Volume Threshold**: Volume multiplier for confirmation (default: 1.5)
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
