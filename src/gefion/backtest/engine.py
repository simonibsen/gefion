"""
Backtesting engine for strategy validation.

Provides point-in-time correct backtesting with no look-ahead bias.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from gefion.backtest.portfolio import Portfolio
from gefion.observability import create_span, set_attributes

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from gefion.backtest.costs import TransactionCosts
    from gefion.backtest.risk import RiskManager
    from gefion.backtest.sizing import PositionSizer
    from gefion.backtest.slippage import SlippageModel


class BacktestEngine:
    """
    Backtesting engine with point-in-time correctness.

    Ensures strategy only has access to past data (no look-ahead bias).

    Optional features (all backward compatible):
    - Transaction costs: Commission, spread, market impact
    - Slippage: Execution price modeling
    - Risk management: Stop loss, take profit, position limits
    - Position sizing: Various sizing methods
    """

    def __init__(
        self,
        price_data: List[Dict[str, Any]],
        strategy: Callable,
        initial_cash: float,
        start_date: date,
        end_date: date,
        *,
        costs: Optional["TransactionCosts"] = None,
        slippage: Optional["SlippageModel"] = None,
        risk_manager: Optional["RiskManager"] = None,
        position_sizer: Optional["PositionSizer"] = None,
        volume_data: Optional[Dict[str, Dict[date, int]]] = None,
        volatility_data: Optional[Dict[str, Dict[date, float]]] = None,
        mode: str = "long_only",
    ):
        """
        Initialize backtesting engine.

        Args:
            price_data: List of price records with symbol, date, close
            strategy: Callable(date, portfolio, prices) -> List[signals]
                      Returns list of dicts with {action, symbol, shares}
            initial_cash: Starting cash amount
            start_date: Start date for backtest
            end_date: End date for backtest
            costs: Optional transaction cost model
            slippage: Optional slippage model
            risk_manager: Optional risk manager for position/portfolio limits
            position_sizer: Optional position sizing strategy
            volume_data: Optional dict {symbol: {date: volume}} for slippage/costs
            volatility_data: Optional dict {symbol: {date: volatility}} for sizing
            mode: 'long_only' (default) or 'long_short' — gates short-side
                  execution (spec 009). 'long_only' is byte-identical to
                  pre-009 behavior; short/cover actions are dropped.
        """
        self.price_data = price_data
        self.strategy = strategy
        self.initial_cash = initial_cash
        self.start_date = start_date
        self.end_date = end_date
        self.mode = mode

        # Optional advanced features
        self.costs = costs
        self.slippage = slippage
        self.risk_manager = risk_manager
        self.position_sizer = position_sizer
        self.volume_data = volume_data or {}
        self.volatility_data = volatility_data or {}

        # Track peak equity for drawdown calculations
        self._peak_equity = initial_cash

        # Short-holding-cost accumulators + margin events (spec 009)
        self._borrow_total = 0.0
        self._dividends_total = 0.0
        self._margin_events: List[Dict[str, Any]] = []

        # Pre-process price data into efficient lookups
        self._prices_by_date = self._index_prices_by_date()
        self._dividends_by_date = self._index_dividends_by_date()
        self._trading_dates = sorted(
            [d for d in self._prices_by_date.keys() if start_date <= d <= end_date]
        )

    def _index_prices_by_date(self) -> Dict[date, Dict[str, float]]:
        """
        Index price data by date for efficient lookups.

        Returns:
            Dict mapping date -> {symbol: price}
        """
        prices_by_date = defaultdict(dict)

        for row in self.price_data:
            row_date = row["date"]
            symbol = row["symbol"]
            close = float(row["close"])
            prices_by_date[row_date][symbol] = close

        return dict(prices_by_date)

    def _index_dividends_by_date(self) -> Dict[date, Dict[str, float]]:
        """Index per-share dividend amounts by date (spec 009 — shorts owe
        dividends to the lender). Absent field → no dividend."""
        divs: Dict[date, Dict[str, float]] = defaultdict(dict)
        for row in self.price_data:
            amt = row.get("dividend_amount")
            if amt:
                divs[row["date"]][row["symbol"]] = float(amt)
        return dict(divs)

    def _accrue_short_holding_costs(
        self, portfolio, current_prices: Dict[str, float], current_date: date
    ) -> None:
        """Charge borrow fees and dividends owed on open shorts for this day.

        Debited from cash so mark-to-market equity reflects them. A no-op when
        there are no shorts (long_only never enters here) — the reproducibility
        gate is preserved.
        """
        day_divs = self._dividends_by_date.get(current_date, {})
        for symbol, position in portfolio.positions.items():
            if position["shares"] >= 0:
                continue
            shares = position["shares"]                 # negative
            price = current_prices.get(symbol, position["avg_price"])
            if self.costs is not None:
                fee = self.costs.daily_borrow_fee(shares, price)
                if fee:
                    portfolio.cash -= fee
                    self._borrow_total += fee
            div = day_divs.get(symbol)
            if div:
                owed = abs(shares) * div
                portfolio.cash -= owed
                self._dividends_total += owed

    def _bar_exposure(self, portfolio, current_prices: Dict[str, float],
                      equity: float, current_date: date) -> Dict[str, Any]:
        """Gross/net/long/short exposure (as fractions of equity) for one bar
        (spec 009). Long-only bars have short == 0."""
        long_notional = 0.0
        short_notional = 0.0
        for symbol, position in portfolio.positions.items():
            price = current_prices.get(symbol, position["avg_price"])
            notional = abs(position["shares"]) * price
            if position["shares"] < 0:
                short_notional += notional
            else:
                long_notional += notional
        denom = equity if equity else 1.0
        return {
            "date": current_date,
            "long": long_notional / denom,
            "short": short_notional / denom,
            "gross": (long_notional + short_notional) / denom,
            "net": (long_notional - short_notional) / denom,
        }

    def _get_historical_prices(self, current_date: date) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get historical prices up to (and including) current date.

        This ensures point-in-time correctness - strategy only sees past data.

        Args:
            current_date: Current date in backtest

        Returns:
            Dict mapping symbol -> list of historical price records
        """
        historical = defaultdict(list)

        for row in self.price_data:
            if row["date"] <= current_date:
                historical[row["symbol"]].append(row)

        return dict(historical)

    def run(self) -> Dict[str, Any]:
        """
        Run backtest and return results.

        Execution order:
        1. Generate risk exit signals (stop loss, take profit)
        2. Get strategy signals
        3. Filter strategy signals through risk manager
        4. Apply position sizing (if configured)
        5. Apply slippage to get execution price
        6. Execute with transaction costs

        Returns:
            Dict with:
                - trades: List of executed trades
                - equity_curve: List of {date, equity} points
                - metrics: Performance metrics
        """
        with create_span(
            "backtest.run",
            initial_cash=self.initial_cash,
            mode=self.mode,
            start_date=str(self.start_date),
            end_date=str(self.end_date),
            trading_days=len(self._trading_dates),
            has_costs=self.costs is not None,
            has_slippage=self.slippage is not None,
            has_risk_manager=self.risk_manager is not None,
            has_position_sizer=self.position_sizer is not None,
        ) as span:
            return self._run_impl(span)

    def _run_impl(self, span) -> Dict[str, Any]:
        """Internal implementation of backtest run."""
        portfolio = Portfolio(initial_cash=self.initial_cash)
        trades = []
        equity_curve = []
        exposure_curve: List[Dict[str, Any]] = []

        for current_date in self._trading_dates:
            # Get current prices for this date
            current_prices = self._prices_by_date.get(current_date, {})

            # Update peak equity for drawdown tracking
            equity = portfolio.calculate_equity(current_prices)
            if equity > self._peak_equity:
                self._peak_equity = equity

            # Get historical data (point-in-time correct)
            historical_prices = self._get_historical_prices(current_date)

            # 1. Generate risk exit signals first (stop loss, take profit)
            exit_signals: List[Dict[str, Any]] = []
            if self.risk_manager:
                exit_signals = self.risk_manager.generate_exit_signals(
                    portfolio, current_prices
                )

            # Execute exit signals first
            for signal in exit_signals:
                executed_trade = self._execute_signal(
                    signal, portfolio, current_prices, current_date
                )
                if executed_trade:
                    trades.append(executed_trade)
                    # A risk-driven forced cover on a losing short is a margin
                    # event (spec 009) — bounds unbounded loss, logged.
                    if (executed_trade.get("action") == "cover"
                            and signal.get("reason") == "stop_loss"):
                        pnl = executed_trade.get("pnl", 0.0)
                        self._margin_events.append({
                            "date": current_date,
                            "symbol": executed_trade["symbol"],
                            "loss": max(0.0, -pnl),
                            "action": "forced_cover",
                        })

            # 2. Get strategy signals
            strategy_signals = self.strategy(
                current_date, portfolio, historical_prices
            )

            # 3. Filter strategy signals through risk manager
            if self.risk_manager:
                strategy_signals = self.risk_manager.filter_signals(
                    strategy_signals, portfolio, current_prices
                )

            # Execute strategy signals
            for signal in strategy_signals:
                executed_trade = self._execute_signal(
                    signal, portfolio, current_prices, current_date
                )
                if executed_trade:
                    trades.append(executed_trade)

            # Short holding costs (borrow fee + dividends owed) accrue for
            # positions held through the day, before equity is marked.
            self._accrue_short_holding_costs(portfolio, current_prices, current_date)

            # Record equity for this date
            equity = portfolio.calculate_equity(current_prices)
            equity_curve.append({"date": current_date, "equity": equity})
            exposure_curve.append(
                self._bar_exposure(portfolio, current_prices, equity, current_date))

        # Calculate metrics
        from gefion.backtest.metrics import calculate_metrics

        metrics = calculate_metrics(equity_curve, initial_capital=self.initial_cash)

        # Add results to span
        set_attributes(
            span,
            trade_count=len(trades),
            total_return=metrics.get("total_return", 0),
            sharpe_ratio=metrics.get("sharpe_ratio", 0),
            max_drawdown=metrics.get("max_drawdown", 0),
            borrow_total=self._borrow_total,
            dividends_total=self._dividends_total,
            margin_events=len(self._margin_events),
        )

        logger.info(
            f"Backtest complete: {len(trades)} trades, "
            f"return={metrics.get('total_return', 0):.2%}, "
            f"sharpe={metrics.get('sharpe_ratio', 0):.2f}"
        )

        return {"trades": trades, "equity_curve": equity_curve,
                "metrics": metrics, "mode": self.mode,
                "short_costs": {"borrow_total": self._borrow_total,
                                "dividends_total": self._dividends_total},
                "margin_events": self._margin_events,
                "exposure": exposure_curve}

    def _execute_signal(
        self,
        signal: Dict[str, Any],
        portfolio: Portfolio,
        current_prices: Dict[str, float],
        current_date: date,
    ) -> Optional[Dict[str, Any]]:
        """
        Execute a single trading signal with costs, slippage, and sizing.

        Args:
            signal: Signal dict with action, symbol, shares
            portfolio: Current portfolio
            current_prices: Current market prices
            current_date: Current date

        Returns:
            Trade dict if executed, None if skipped
        """
        action = signal.get("action")
        symbol = signal.get("symbol")
        shares = signal.get("shares", 0)

        # Short-side actions require long_short mode; in long_only they are
        # dropped (like any unrecognized action) — the reproducibility gate.
        if action in ("short", "cover") and self.mode != "long_short":
            return None

        if symbol not in current_prices:
            return None

        price = current_prices[symbol]

        # Market direction for slippage/cost purposes: a short is a sell, a
        # cover is a buy.
        market_action = {"short": "sell", "cover": "buy"}.get(action, action)

        # Get volume and volatility if available
        daily_volume = self.volume_data.get(symbol, {}).get(current_date)
        volatility = self.volatility_data.get(symbol, {}).get(current_date)

        # 4. Apply position sizing for buy orders
        if action == "buy" and self.position_sizer:
            portfolio_value = portfolio.calculate_equity(current_prices)
            shares = self.position_sizer.calculate_shares(
                portfolio_value=portfolio_value,
                price=price,
                symbol=symbol,
                volatility=volatility,
            )
            if shares <= 0:
                return None

        # 5. Apply slippage to get execution price
        execution_price = price
        if self.slippage:
            slipped_price = self.slippage.calculate_execution_price(
                order_price=price,
                shares=shares,
                action=market_action,
                daily_volume=daily_volume,
                volatility=volatility,
            )
            if slipped_price is None:
                # Limit order didn't fill
                return None
            execution_price = slipped_price

        # 6. Execute with transaction costs
        try:
            if action == "buy":
                portfolio.buy(
                    symbol=symbol,
                    shares=shares,
                    price=execution_price,
                    date=current_date,
                    costs=self.costs,
                    daily_volume=daily_volume,
                )
            elif action == "sell":
                # For sells, ensure we don't sell more than we have
                position = portfolio.get_position(symbol)
                shares = min(shares, int(position.get("shares", 0)))
                if shares <= 0:
                    return None
                # Capture cost basis before the sell mutates the position
                avg_cost = float(position.get("avg_price", 0.0))

                portfolio.sell(
                    symbol=symbol,
                    shares=shares,
                    price=execution_price,
                    date=current_date,
                    costs=self.costs,
                    daily_volume=daily_volume,
                )
            elif action == "short":
                portfolio.short(
                    symbol=symbol,
                    shares=shares,
                    price=execution_price,
                    date=current_date,
                    costs=self.costs,
                    daily_volume=daily_volume,
                )
            elif action == "cover":
                # Don't cover more than the open short.
                position = portfolio.get_position(symbol)
                open_short = -int(position.get("shares", 0))
                shares = min(shares, open_short)
                if shares <= 0:
                    return None
                # Capture entry before the cover mutates the position.
                avg_entry = float(position.get("avg_price", 0.0))
                portfolio.cover(
                    symbol=symbol,
                    shares=shares,
                    price=execution_price,
                    date=current_date,
                    costs=self.costs,
                    daily_volume=daily_volume,
                )
            else:
                return None

            # Build trade record
            trade = {
                "date": current_date,
                "action": action,
                "symbol": symbol,
                "shares": shares,
                "price": execution_price,
            }
            if action == "sell":
                # Realized pnl — trade metrics (win_rate, profit_factor)
                # are meaningless without it
                trade["pnl"] = (execution_price - avg_cost) * shares
            elif action == "cover":
                # A short profits when the price falls: entry − exit.
                trade["pnl"] = (avg_entry - execution_price) * shares
            if action in ("short", "cover"):
                trade["side"] = "short"
            if signal.get("reason"):
                trade["reason"] = signal["reason"]
            if self.slippage and execution_price != price:
                trade["slippage"] = execution_price - price
            if self.costs:
                trade["cost"] = self.costs.calculate_cost(
                    shares, execution_price, market_action, daily_volume
                )

            return trade

        except ValueError:
            # Insufficient cash/shares
            return None
