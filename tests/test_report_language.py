from pathlib import Path


def test_all_report_documents_declare_vietnamese_language():
    reporting_dir = (
        Path(__file__).resolve().parents[1] / "src" / "infrastructure" / "reporting"
    )
    report_documents = list(reporting_dir.glob("templates/*/html_template.html"))
    report_documents.extend(reporting_dir.glob("templates/*/image_template.html"))
    report_documents.append(
        reporting_dir / "platform_templates" / "qq_official" / "summary_dashboard.html"
    )

    assert report_documents
    for document in report_documents:
        html = document.read_text(encoding="utf-8")
        assert '<html lang="vi">' in html, document
