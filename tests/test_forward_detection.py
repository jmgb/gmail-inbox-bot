"""Tests for forwarded email detection and original sender extraction."""

from gmail_inbox_bot.mail_processing import _is_forwarded_email, extract_original_sender


class TestIsForwardedEmail:
    def test_matches_domain_pattern(self):
        config = {"forwarded_from": ["@pactomundial.org"]}
        assert _is_forwarded_email("asociacion@pactomundial.org", config) is True
        assert _is_forwarded_email("contabilidad@pactomundial.org", config) is True

    def test_no_match_external(self):
        config = {"forwarded_from": ["@pactomundial.org"]}
        assert _is_forwarded_email("juan@empresa.com", config) is False

    def test_empty_config(self):
        assert _is_forwarded_email("asociacion@pactomundial.org", {}) is False

    def test_case_insensitive(self):
        config = {"forwarded_from": ["@PactoMundial.org"]}
        assert _is_forwarded_email("ASOCIACION@pactomundial.org", config) is True

    def test_multiple_patterns(self):
        config = {"forwarded_from": ["@pactomundial.org", "@external.com"]}
        assert _is_forwarded_email("user@external.com", config) is True
        assert _is_forwarded_email("user@other.com", config) is False


class TestExtractOriginalSender:
    def test_outlook_html_spanish(self):
        html = (
            "<hr><div><font><b>De:</b> Juan García &lt;juan@empresa.com&gt;<br>"
            "<b>Enviado:</b> lunes, 24 de febrero de 2026<br></font></div>"
        )
        result = extract_original_sender(html)
        assert result == {"name": "Juan García", "address": "juan@empresa.com"}

    def test_outlook_html_english(self):
        html = "<b>From:</b> Maria Silva &lt;maria@empresa.pt&gt;<br>"
        result = extract_original_sender(html)
        assert result == {"name": "Maria Silva", "address": "maria@empresa.pt"}

    def test_no_forwarding_headers(self):
        html = "<p>Hola, quiero información sobre el programa.</p>"
        assert extract_original_sender(html) is None

    def test_without_bold_tags(self):
        html = "De: Pedro López &lt;pedro@test.org&gt;<br>Enviado: ..."
        result = extract_original_sender(html)
        assert result == {"name": "Pedro López", "address": "pedro@test.org"}

    def test_name_with_accents(self):
        html = "<b>De:</b> José María Ñoño &lt;jose@test.es&gt;<br>"
        result = extract_original_sender(html)
        assert result is not None
        assert result["address"] == "jose@test.es"
        assert result["name"] == "José María Ñoño"

    def test_address_lowercased(self):
        html = "<b>De:</b> Test &lt;USER@EMPRESA.COM&gt;<br>"
        result = extract_original_sender(html)
        assert result is not None
        assert result["address"] == "user@empresa.com"

    def test_complex_outlook_forward(self):
        """Real-world Outlook forward with multiple divs and styling."""
        html = """
        <html><body>
        <hr style="display:inline-block;width:98%" tabindex="-1">
        <div id="divRplyFwdMsg" dir="ltr">
        <font face="Calibri, sans-serif" style="font-size:11pt" color="#000000">
        <b>De:</b> Ana López Martínez &lt;ana.lopez@empresa-grande.com&gt;<br>
        <b>Enviado:</b> martes, 25 de febrero de 2026 14:30<br>
        <b>Para:</b> asociacion@pactomundial.org &lt;asociacion@pactomundial.org&gt;<br>
        <b>Asunto:</b> Consulta programa Proveedores Sostenibles<br>
        </font></div>
        <p>Hola, me gustaría información sobre el programa de proveedores sostenibles.</p>
        </body></html>
        """
        result = extract_original_sender(html)
        assert result is not None
        assert result["name"] == "Ana López Martínez"
        assert result["address"] == "ana.lopez@empresa-grande.com"

    def test_invalid_email_rejected(self):
        """Email without domain dot should be rejected."""
        html = "<b>De:</b> Bad &lt;bad@nodot&gt;<br>"
        assert extract_original_sender(html) is None
