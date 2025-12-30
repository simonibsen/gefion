"""Backtesting page - Test trading strategies."""

import streamlit as st
import subprocess
import sys
from datetime import datetime, date, timedelta


def render_backtest():
    """Render the backtesting page."""
    st.title("📈 Backtesting")
    st.markdown("Test trading strategies on historical data with realistic execution modeling.")

    tab1, tab2, tab3 = st.tabs(["🎮 Run Backtest", "⚔️ Compare Strategies", "📊 Results"])

    with tab1:
        render_run_section()

    with tab2:
        render_compare_section()

    with tab3:
        render_results_section()


def render_run_section():
    """Render backtest execution section."""
    st.subheader("Run Backtest")

    st.info("""
    💡 **Backtesting** simulates trading a strategy on historical data.
    Results include returns, Sharpe ratio, max drawdown, and trade statistics.
    """)

    col1, col2 = st.columns(2)

    with col1:
        strategy = st.selectbox(
            "Strategy",
            ["momentum", "mean_reversion", "ma_crossover", "breakout"],
            help="""
            - **momentum**: Buy winners, sell losers
            - **mean_reversion**: Buy oversold, sell overbought
            - **ma_crossover**: Follow moving average signals
            - **breakout**: Buy on price breakouts
            """,
        )

        # Date range
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
            help="Starting portfolio value",
        )

    with col2:
        # Symbol selection
        from g2.ui.components.database import get_symbols
        symbols = get_symbols()

        symbol_mode = st.radio(
            "Symbols",
            ["Selected", "Exchange"],
            horizontal=True,
        )

        if symbol_mode == "Selected":
            selected_symbols = st.multiselect(
                "Select Symbols",
                symbols,
                default=symbols[:10] if len(symbols) >= 10 else symbols,
            )
        else:
            exchange = st.selectbox(
                "Exchange",
                ["NASDAQ", "NYSE"],
                key="bt_exchange",
            )
            bt_limit = st.number_input(
                "Limit",
                min_value=10,
                max_value=200,
                value=50,
                key="bt_limit",
            )

    # Execution modeling
    st.markdown("##### Execution Settings")
    st.caption("Model realistic trading costs and constraints")

    col1, col2, col3 = st.columns(3)

    with col1:
        cost_preset = st.selectbox(
            "Cost Model",
            ["zero", "retail", "institutional"],
            index=1,
            help="""
            - **zero**: No costs (theoretical)
            - **retail**: Typical retail costs
            - **institutional**: Lower institutional costs
            """,
        )

    with col2:
        slippage_preset = st.selectbox(
            "Slippage Model",
            ["zero", "realistic"],
            index=1,
            help="Model price impact of trades",
        )

    with col3:
        risk_preset = st.selectbox(
            "Risk Controls",
            ["none", "conservative", "aggressive"],
            index=0,
            help="Stop loss and take profit settings",
        )

    # Position sizing
    col1, col2 = st.columns(2)

    with col1:
        sizing_method = st.selectbox(
            "Position Sizing",
            ["fixed_dollar", "fixed_percent", "kelly", "volatility_target"],
            help="""
            - **fixed_dollar**: Fixed $ per trade
            - **fixed_percent**: Fixed % of portfolio
            - **kelly**: Kelly criterion
            - **volatility_target**: Adjust for volatility
            """,
        )

    with col2:
        if sizing_method == "fixed_dollar":
            sizing_amount = st.number_input(
                "Amount per Trade ($)",
                min_value=100,
                max_value=100000,
                value=10000,
            )
        elif sizing_method == "fixed_percent":
            sizing_amount = st.slider(
                "Portfolio %",
                min_value=1,
                max_value=25,
                value=5,
            )
        else:
            sizing_amount = st.number_input(
                "Target (varies by method)",
                min_value=1.0,
                max_value=100.0,
                value=10.0,
            )

    if st.button("🚀 Run Backtest", type="primary", use_container_width=True):
        with st.spinner("Running backtest..."):
            try:
                import os
                env = os.environ.copy()
                env["OTEL_ENABLED"] = "false"

                cmd = [
                    sys.executable, "-m", "g2.cli", "backtest", "run",
                    "--strategy", strategy,
                    "--start-date", str(start_date),
                    "--end-date", str(end_date),
                    "--initial-cash", str(initial_cash),
                    "--cost-preset", cost_preset,
                    "--slippage-preset", slippage_preset,
                    "--risk-preset", risk_preset,
                    "--sizing-method", sizing_method,
                    "--sizing-amount", str(sizing_amount),
                    "--json",
                ]

                if symbol_mode == "Selected":
                    cmd.extend(["--symbols", ",".join(selected_symbols)])
                else:
                    cmd.extend(["--exchange", exchange, "--limit", str(bt_limit)])

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=600,
                )

                if result.returncode == 0:
                    st.success("✅ Backtest complete!")

                    # Parse and display results
                    try:
                        import json
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
                    st.error("❌ Backtest failed")
                    st.code(result.stderr)

            except Exception as e:
                st.error(f"Error: {e}")


def render_compare_section():
    """Render strategy comparison section."""
    st.subheader("Compare Strategies")

    st.info("💡 Compare multiple strategies on the same data to find the best performer.")

    col1, col2 = st.columns(2)

    with col1:
        strategies = st.multiselect(
            "Strategies to Compare",
            ["momentum", "mean_reversion", "ma_crossover", "breakout"],
            default=["momentum", "mean_reversion"],
            help="Select 2+ strategies to compare",
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
            default=symbols[:20] if len(symbols) >= 20 else symbols,
            key="cmp_symbols",
        )

        rank_by = st.selectbox(
            "Rank By",
            ["sharpe_ratio", "total_return", "max_drawdown", "win_rate"],
            help="Metric to rank strategies by",
        )

    if len(strategies) < 2:
        st.warning("Select at least 2 strategies to compare.")
        return

    if st.button("⚔️ Compare", type="primary", use_container_width=True):
        with st.spinner("Comparing strategies..."):
            try:
                import os
                env = os.environ.copy()
                env["OTEL_ENABLED"] = "false"

                cmd = [
                    sys.executable, "-m", "g2.cli", "backtest", "compare",
                    "--strategies", ",".join(strategies),
                    "--start-date", str(start_date),
                    "--end-date", str(end_date),
                    "--symbols", ",".join(selected_symbols),
                    "--rank-by", rank_by,
                    "--json",
                ]

                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=600,
                )

                if result.returncode == 0:
                    st.success("✅ Comparison complete!")

                    try:
                        import json
                        data = json.loads(result.stdout)

                        if "comparison" in data:
                            import pandas as pd
                            df = pd.DataFrame(data["comparison"])
                            st.dataframe(df, use_container_width=True)
                    except Exception:
                        pass

                    with st.expander("Full Output"):
                        st.code(result.stdout)
                else:
                    st.error("❌ Comparison failed")
                    st.code(result.stderr)

            except Exception as e:
                st.error(f"Error: {e}")


def render_results_section():
    """Render backtest results browser."""
    st.subheader("Previous Results")

    st.info("💡 View and analyze results from previous backtests.")

    # In a full implementation, this would load from database
    # For now, show placeholder

    st.markdown("""
    ### Available Metrics

    | Metric | Description |
    |--------|-------------|
    | **Total Return** | Overall portfolio gain/loss |
    | **Sharpe Ratio** | Risk-adjusted return (>1 is good, >2 is excellent) |
    | **Max Drawdown** | Largest peak-to-trough decline |
    | **Win Rate** | Percentage of profitable trades |
    | **Profit Factor** | Gross profit / Gross loss |
    | **Avg Trade** | Average profit per trade |

    ### Interpreting Results

    - **Sharpe > 1.0**: Decent risk-adjusted returns
    - **Sharpe > 2.0**: Excellent risk-adjusted returns
    - **Max DD < 20%**: Acceptable drawdown for most strategies
    - **Win Rate > 50%**: More winners than losers

    ⚠️ Past performance doesn't guarantee future results.
    """)

    # Strategy descriptions
    with st.expander("📚 Strategy Descriptions"):
        st.markdown("""
        ### Momentum
        Buys stocks that have been going up, expecting continuation.
        - Lookback: 20 days
        - Entry: Top performers
        - Exit: When momentum fades

        ### Mean Reversion
        Buys oversold stocks, expecting bounce back.
        - Uses RSI and Bollinger Bands
        - Entry: Oversold conditions
        - Exit: Return to mean

        ### MA Crossover
        Follows moving average signals.
        - Fast MA: 20 days
        - Slow MA: 50 days
        - Buy on golden cross, sell on death cross

        ### Breakout
        Buys when price breaks above resistance.
        - Lookback: 20 days for range
        - Entry: Break above high
        - Exit: Stop loss or take profit
        """)
