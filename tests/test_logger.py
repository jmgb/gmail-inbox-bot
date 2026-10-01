"""El nivel de los handlers lo gobierna LOG_LEVEL, no un DEBUG fijo.

El caso que importa: a nivel DEBUG se volcaban al fichero el JSON del clasificador y
las métricas pasara lo que pasara, porque el handler ignoraba LOG_LEVEL. Con
LOG_LEVEL=INFO nada de nivel DEBUG debe llegar a app.log, que se descarga desde
/admin/logs.
"""

import logging
from logging.handlers import RotatingFileHandler

from gmail_inbox_bot.logger import setup_logger


def _file_levels(logger: logging.Logger) -> list[int]:
    return [h.level for h in logger.handlers if isinstance(h, RotatingFileHandler)]


def test_debug_no_llega_al_fichero_con_log_level_info(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    log_file = tmp_path / "app.log"
    logger = setup_logger("test_logger.info_oculta_debug", str(log_file))

    logger.debug("Clasificacion JSON completo: %s", {"ultimo_email": "CUERPO-SENSIBLE"})
    logger.info("Clasificacion: categoria=spam")
    for handler in logger.handlers:
        handler.flush()

    contenido = log_file.read_text(encoding="utf-8")
    assert "CUERPO-SENSIBLE" not in contenido
    assert "categoria=spam" in contenido


def test_log_level_debug_si_lo_pides_explicitamente(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    log_file = tmp_path / "app.log"
    logger = setup_logger("test_logger.debug_explicito", str(log_file))

    logger.debug("cuerpo en modo depuracion")
    for handler in logger.handlers:
        handler.flush()

    assert "cuerpo en modo depuracion" in log_file.read_text(encoding="utf-8")


def test_log_level_por_defecto_es_info(tmp_path, monkeypatch):
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    logger = setup_logger("test_logger.por_defecto", str(tmp_path / "app.log"))

    assert _file_levels(logger) == [logging.INFO]


def test_log_level_invalido_cae_a_info(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "no-es-un-nivel")
    logger = setup_logger("test_logger.invalido", str(tmp_path / "app.log"))

    assert _file_levels(logger) == [logging.INFO]


def test_debug_mode_manda_sobre_log_level(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    logger = setup_logger("test_logger.debug_mode_gana", str(tmp_path / "app.log"), debug_mode=True)

    assert _file_levels(logger) == [logging.DEBUG]


def test_los_loggers_del_mismo_fichero_comparten_un_unico_handler(tmp_path, monkeypatch):
    """Dos RotatingFileHandler sobre el mismo fichero rotan cada uno por su cuenta: tras la
    primera rotación el resto sigue escribiendo en el fichero renombrado."""
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    log_file = str(tmp_path / "app.log")
    uno = setup_logger("test_logger.compartido_uno", log_file)
    dos = setup_logger("test_logger.compartido_dos", log_file)

    handlers_uno = [h for h in uno.handlers if isinstance(h, RotatingFileHandler)]
    handlers_dos = [h for h in dos.handlers if isinstance(h, RotatingFileHandler)]
    assert len(handlers_uno) == 1
    assert handlers_uno[0] is handlers_dos[0]
