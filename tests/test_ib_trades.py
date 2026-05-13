"""Tests for IB trade parser and Telegram notification."""

from unittest.mock import patch

from gmail_inbox_bot.ib_trades import (
    Trade,
    notify_trade,
    parse_trade,
)


class TestParseTrade:
    def test_sold_basic(self):
        trade = parse_trade("SOLD 1,511 VEEA @ 0.5722 (UXXX55709)")
        assert trade is not None
        assert trade.side == "SOLD"
        assert trade.quantity == 1511
        assert trade.ticker == "VEEA"
        assert trade.price == 0.5722
        assert trade.account == "UXXX55709"

    def test_sold_no_comma(self):
        trade = parse_trade("SOLD 300 VEEA @ 0.5722 (UXXX55709)")
        assert trade is not None
        assert trade.side == "SOLD"
        assert trade.quantity == 300

    def test_bot_is_buy(self):
        """IB uses 'BOT' for buy orders."""
        trade = parse_trade("BOT 500 AAPL @ 182.50 (U1234567)")
        assert trade is not None
        assert trade.side == "BUY"
        assert trade.quantity == 500
        assert trade.ticker == "AAPL"
        assert trade.price == 182.50

    def test_buy_keyword(self):
        trade = parse_trade("BUY 100 TSLA @ 250.00 (U1234567)")
        assert trade is not None
        assert trade.side == "BUY"

    def test_bought_keyword(self):
        trade = parse_trade("BOUGHT 100 MSFT @ 400.00 (U1234567)")
        assert trade is not None
        assert trade.side == "BUY"

    def test_no_account(self):
        trade = parse_trade("SOLD 1,000 SPY @ 450.25")
        assert trade is not None
        assert trade.account == ""
        assert trade.quantity == 1000

    def test_no_match(self):
        assert parse_trade("Welcome to Interactive Brokers") is None

    def test_empty_string(self):
        assert parse_trade("") is None

    def test_ticker_with_dot(self):
        trade = parse_trade("SOLD 100 BRK.B @ 350.00 (U999)")
        assert trade is not None
        assert trade.ticker == "BRK.B"

    def test_timestamp_present(self):
        trade = parse_trade("SOLD 100 AAPL @ 150.00 (U123)")
        assert trade is not None
        assert "T" in trade.timestamp  # ISO format


class TestNotifyTrade:
    @patch("gmail_inbox_bot.ib_trades.enviar_mensaje_telegram")
    def test_sell_notification(self, mock_send):
        trade = Trade(
            side="SOLD",
            quantity=1511,
            ticker="VEEA",
            price=0.5722,
            account="UXXX55709",
            timestamp="2026-03-12T15:37:00+01:00",
        )
        notify_trade(trade, "miguel@gmail.com")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        assert "SOLD" in msg
        assert "VEEA" in msg
        assert "1,511" in msg
        assert "0.5722" in msg
        assert "UXXX55709" in msg
        assert "\U0001f534" in msg  # red circle for SOLD

    @patch("gmail_inbox_bot.ib_trades.enviar_mensaje_telegram")
    def test_buy_notification(self, mock_send):
        trade = Trade(
            side="BUY",
            quantity=500,
            ticker="AAPL",
            price=182.50,
            account="U123",
            timestamp="2026-03-12T15:37:00+01:00",
        )
        notify_trade(trade, "test@gmail.com")
        msg = mock_send.call_args[0][0]
        assert "BUY" in msg
        assert "\U0001f7e2" in msg  # green circle for BUY
