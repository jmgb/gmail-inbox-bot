"""Tests for pre-filter logic in bot.py."""

from unittest.mock import patch

from gmail_inbox_bot.mail_processing import apply_pre_filters


class TestApplyPreFilters:
    def test_no_filters_returns_none(self, graph, config, email_msg):
        # config has no pre_filters key
        result = apply_pre_filters(graph, config, email_msg, dry_run=False)
        assert result is None

    def test_empty_filters_returns_none(self, graph, config, email_msg):
        config["pre_filters"] = []
        result = apply_pre_filters(graph, config, email_msg, dry_run=False)
        assert result is None

    def test_sender_contains_match_silent(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Block Ariba",
                "match": {"sender_contains": ["@ansmtp.ariba.com"]},
                "action": "silent",
            }
        ]
        msg = make_email(sender_address="notifications@ansmtp.ariba.com")

        match = apply_pre_filters(graph, config, msg, dry_run=False)

        assert match is not None
        result, name = match
        assert "silent" in result
        assert name == "Block Ariba"
        graph.update_email.assert_called_once_with("test@example.com", msg["id"], is_read=True)

    def test_sender_contains_no_match(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Block Ariba",
                "match": {"sender_contains": ["@ariba.com"]},
                "action": "silent",
            }
        ]
        msg = make_email(sender_address="juan@empresa.com")

        result = apply_pre_filters(graph, config, msg, dry_run=False)

        assert result is None

    def test_sender_contains_string_not_list(self, graph, config, make_email):
        """sender_contains can be a single string instead of a list."""
        config["pre_filters"] = [
            {
                "name": "Internal",
                "match": {"sender_contains": "@pactomundial.org"},
                "action": "silent",
            }
        ]
        msg = make_email(sender_address="laura@pactomundial.org")

        result, _name = apply_pre_filters(graph, config, msg, dry_run=False)

        assert "silent" in result

    def test_sender_not_contains_excludes(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Internal except self",
                "match": {
                    "sender_contains": "@pactomundial.org",
                    "sender_not_contains": "contabilidad@pactomundial.org",
                },
                "action": "tag",
                "tag": "PENDIENTE GESTIONAR",
            }
        ]
        # Self email should NOT match
        msg = make_email(sender_address="contabilidad@pactomundial.org")
        result = apply_pre_filters(graph, config, msg, dry_run=False)
        assert result is None

        # Other internal SHOULD match
        graph.reset_mock()
        msg2 = make_email(sender_address="laura@pactomundial.org")
        result2, _name = apply_pre_filters(graph, config, msg2, dry_run=False)
        assert "PENDIENTE GESTIONAR" in result2

    def test_tag_and_move_action(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Internal",
                "match": {"sender_contains": "@pactomundial.org"},
                "action": "tag_and_move",
                "tag": "INTERNO",
                "folder": "Internos",
            }
        ]
        msg = make_email(sender_address="laura@pactomundial.org")

        result, _name = apply_pre_filters(graph, config, msg, dry_run=False)

        assert "INTERNO" in result
        assert "Internos" in result
        graph.update_email.assert_called_once()
        graph.move_email.assert_called_once_with(
            "test@example.com", msg["id"], "Internos", parent_folder=None
        )

    def test_delete_action(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Kill spam",
                "match": {"sender_contains": "@spam.com"},
                "action": "delete",
            }
        ]
        msg = make_email(sender_address="news@spam.com")

        result, _name = apply_pre_filters(graph, config, msg, dry_run=False)

        assert "delete" in result
        graph.delete_email.assert_called_once_with("test@example.com", msg["id"])

    def test_first_match_wins(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Filter A",
                "match": {"sender_contains": "@empresa.com"},
                "action": "silent",
            },
            {
                "name": "Filter B",
                "match": {"sender_contains": "@empresa.com"},
                "action": "delete",
            },
        ]
        msg = make_email(sender_address="juan@empresa.com")

        result, name = apply_pre_filters(graph, config, msg, dry_run=False)

        assert "Filter A" in result
        assert "silent" in result
        assert name == "Filter A"
        graph.delete_email.assert_not_called()

    def test_dry_run_no_actions(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Block Ariba",
                "match": {"sender_contains": "@ariba.com"},
                "action": "silent",
            }
        ]
        msg = make_email(sender_address="x@ariba.com")

        result, _name = apply_pre_filters(graph, config, msg, dry_run=True)

        assert "[DRY-RUN]" in result
        graph.update_email.assert_not_called()

    def test_case_insensitive_matching(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Internal",
                "match": {"sender_contains": "@PactoMundial.org"},
                "action": "silent",
            }
        ]
        msg = make_email(sender_address="LAURA@pactomundial.ORG")

        match = apply_pre_filters(graph, config, msg, dry_run=False)

        assert match is not None
        result, _name = match
        assert "silent" in result

    # ---- subject_contains / subject_not_contains ----

    def test_subject_contains_match(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Bounce NDR",
                "match": {"subject_contains": ["No se puede entregar:", "Undeliverable:"]},
                "action": "silent",
            }
        ]
        msg = make_email(subject="No se puede entregar: SAVE THE DATE")

        match = apply_pre_filters(graph, config, msg, dry_run=False)

        assert match is not None
        result, _name = match
        assert "silent" in result

    def test_subject_contains_no_match(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Bounce NDR",
                "match": {"subject_contains": ["No se puede entregar:", "Undeliverable:"]},
                "action": "silent",
            }
        ]
        msg = make_email(subject="Consulta sobre el programa")

        result = apply_pre_filters(graph, config, msg, dry_run=False)

        assert result is None

    def test_subject_contains_case_insensitive(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Bounce EN",
                "match": {"subject_contains": "Undeliverable:"},
                "action": "silent",
            }
        ]
        msg = make_email(subject="UNDELIVERABLE: Your message")

        match = apply_pre_filters(graph, config, msg, dry_run=False)

        assert match is not None
        result, _name = match
        assert "silent" in result

    def test_subject_not_contains_excludes(self, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "Bounce except urgent",
                "match": {
                    "subject_contains": "Undeliverable:",
                    "subject_not_contains": "URGENT",
                },
                "action": "silent",
            }
        ]
        msg = make_email(subject="Undeliverable: URGENT notification")
        result = apply_pre_filters(graph, config, msg, dry_run=False)
        assert result is None

        graph.reset_mock()
        msg2 = make_email(subject="Undeliverable: Regular bounce")
        match2 = apply_pre_filters(graph, config, msg2, dry_run=False)
        assert match2 is not None
        result2, _name = match2
        assert "silent" in result2

    def test_sender_and_subject_combined(self, graph, config, make_email):
        """Both sender_contains and subject_contains must match (AND logic)."""
        config["pre_filters"] = [
            {
                "name": "Postmaster bounce",
                "match": {
                    "sender_contains": "postmaster@",
                    "subject_contains": "Undeliverable:",
                },
                "action": "silent",
            }
        ]
        # Both match
        msg = make_email(
            sender_address="postmaster@outlook.com",
            subject="Undeliverable: Your message",
        )
        match = apply_pre_filters(graph, config, msg, dry_run=False)
        assert match is not None

        # Only sender matches
        graph.reset_mock()
        msg2 = make_email(
            sender_address="postmaster@outlook.com",
            subject="Regular subject",
        )
        result2 = apply_pre_filters(graph, config, msg2, dry_run=False)
        assert result2 is None

        # Only subject matches
        graph.reset_mock()
        msg3 = make_email(
            sender_address="juan@empresa.com",
            subject="Undeliverable: Your message",
        )
        result3 = apply_pre_filters(graph, config, msg3, dry_run=False)
        assert result3 is None

    # ---- ib_trade action ----

    @patch("gmail_inbox_bot.mail_processing.notify_trade")
    def test_ib_trade_parses_and_notifies(self, mock_notify, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "IB Trading",
                "match": {"sender_contains": "tradingassistant@interactivebrokers.com"},
                "action": "ib_trade",
                "folder": "Trading",
            }
        ]
        msg = make_email(
            sender_address="TradingAssistant@interactivebrokers.com",
            subject="SOLD 1,511 VEEA @ 0.5722 (UXXX55709)",
        )

        result, name = apply_pre_filters(graph, config, msg, dry_run=False)

        assert "ib_trade" in result
        assert "SOLD" in result
        assert name == "IB Trading"
        mock_notify.assert_called_once()
        trade = mock_notify.call_args[0][0]
        assert trade.side == "SOLD"
        assert trade.quantity == 1511
        assert trade.ticker == "VEEA"
        assert trade.price == 0.5722
        graph.update_email.assert_called_once()
        graph.move_email.assert_called_once()

    @patch("gmail_inbox_bot.mail_processing.notify_trade")
    def test_ib_trade_unparseable_still_processes(self, mock_notify, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "IB Trading",
                "match": {"sender_contains": "tradingassistant@interactivebrokers.com"},
                "action": "ib_trade",
                "folder": "Trading",
            }
        ]
        msg = make_email(
            sender_address="TradingAssistant@interactivebrokers.com",
            subject="Your daily statement is ready",
        )

        result, _name = apply_pre_filters(graph, config, msg, dry_run=False)

        assert "ib_trade" in result
        mock_notify.assert_not_called()
        graph.update_email.assert_called_once()
        graph.move_email.assert_called_once()

    @patch("gmail_inbox_bot.mail_processing.notify_trade")
    def test_ib_trade_no_folder(self, mock_notify, graph, config, make_email):
        config["pre_filters"] = [
            {
                "name": "IB Trading",
                "match": {"sender_contains": "tradingassistant@interactivebrokers.com"},
                "action": "ib_trade",
            }
        ]
        msg = make_email(
            sender_address="TradingAssistant@interactivebrokers.com",
            subject="BOT 500 AAPL @ 182.50 (U123)",
        )

        result, _name = apply_pre_filters(graph, config, msg, dry_run=False)

        assert "ib_trade" in result
        mock_notify.assert_called_once()
        graph.update_email.assert_called_once()
        graph.move_email.assert_not_called()
