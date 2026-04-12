from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.quality_gate import classify_source_quality, evaluate_screenshot_quality


def test_source_filter_labels_login_and_error() -> None:
    login_label = classify_source_quality(
        url="https://instagram.com/accounts/login",
        page_title="Entrar no Instagram",
        visible_claims=["Faca login para continuar"],
        raw_text="Entrar no Instagram",
    )
    error_label = classify_source_quality(
        url="https://example.com/404",
        page_title="Erro 404",
        visible_claims=["Pagina nao encontrada"],
        raw_text="Erro 404",
    )
    assert login_label == "login_wall"
    assert error_label == "error_page"


def test_source_filter_labels_scr_page_as_institutional() -> None:
    label = classify_source_quality(
        url="https://www.itau.com.br/emprestimos-financiamentos/sistema-de-informacoes-de-credito",
        page_title="Sistema de Informacoes de Credito - SCR | Itau",
        visible_claims=["Saiba como funciona o Sistema de Informacoes de Credito", "Consulte detalhes e atendimento"],
        raw_text="Informacoes institucionais sobre o SCR, privacidade, atendimento, termos de uso e politicas da instituicao.",
    )
    assert label == "institutional"


def test_screenshot_quality_detects_blank(tmp_path: Path) -> None:
    white_img = tmp_path / "white.png"
    Image.new("RGB", (1200, 900), color=(255, 255, 255)).save(white_img)
    score, reasons = evaluate_screenshot_quality(white_img, visible_claims=[], raw_text="")
    assert score < 0.7
    assert "screenshot_mostly_white" in reasons
