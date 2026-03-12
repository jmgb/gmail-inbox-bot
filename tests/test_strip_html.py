"""Tests for strip_html() in bot.py."""

from gmail_inbox_bot.mail_processing import strip_html


class TestStripHtml:
    def test_br_to_newline(self):
        assert strip_html("Hola<br>Mundo") == "Hola\nMundo"

    def test_br_slash_to_newline(self):
        assert strip_html("Hola<br/>Mundo") == "Hola\nMundo"
        assert strip_html("Hola<br />Mundo") == "Hola\nMundo"

    def test_br_case_insensitive(self):
        assert strip_html("Hola<BR>Mundo") == "Hola\nMundo"

    def test_html_tags_removed(self):
        result = strip_html("<html><body><p>Hola</p></body></html>")
        assert "<" not in result
        assert "Hola" in result

    def test_html_entities_decoded(self):
        assert "&" in strip_html("Tom &amp; Jerry")
        assert strip_html("1 &lt; 2") == "1 < 2"
        assert strip_html("&quot;hola&quot;") == '"hola"'

    def test_multiple_newlines_collapsed(self):
        result = strip_html("A\n\n\n\n\nB")
        assert result == "A\n\nB"

    def test_empty_string(self):
        assert strip_html("") == ""

    def test_plain_text_unchanged(self):
        assert strip_html("Hola mundo") == "Hola mundo"

    def test_complex_outlook_html(self):
        html = """
        <html>
        <head><style>body{font-family:Calibri}</style></head>
        <body>
        <div>
            <p>Hola,</p>
            <p>¿El programa es gratuito?</p>
            <br>
            <p>Saludos,<br/>Juan</p>
        </div>
        <div class="signature">
            <p>--</p>
            <p>Juan García | Empresa S.L.</p>
        </div>
        </body>
        </html>
        """
        result = strip_html(html)
        assert "Hola," in result
        assert "gratuito" in result
        assert "Juan" in result
        assert "<div>" not in result
        assert "<style>" not in result

    def test_nested_tags(self):
        result = strip_html("<div><span><b>Negrita</b></span></div>")
        assert result == "Negrita"
