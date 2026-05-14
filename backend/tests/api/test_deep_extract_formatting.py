from __future__ import annotations

from types import SimpleNamespace

from app.api.v1 import studio


def test_schema_type_labels_handles_jsonld_graph_and_marker_formats() -> None:
    labels = studio._schema_type_labels({
        "__format": "json-ld",
        "@graph": [
            {"@type": "Product"},
            {"@type": ["Offer", "AggregateRating"]},
        ],
    })

    assert labels == ["Product", "Offer", "AggregateRating"]
    assert studio._schema_type_labels({
        "__format": "microdata",
        "@type": "https://schema.org/Product",
    }) == ["https://schema.org/Product"]
    assert studio._schema_type_labels({
        "__format": "rdfa",
        "typeof": "BreadcrumbList",
    }) == ["BreadcrumbList"]


def test_format_extract_marks_speed_as_lab_and_keeps_schema_formats() -> None:
    extract = SimpleNamespace(
        url="https://example.com/page",
        is_competitor=False,
        js_errors=[],
        title="Title",
        h1="H1",
        meta_description="Meta",
        full_text="Visible text",
        headings_tree=[],
        performance={"lcp": 1200, "fcp": 700, "cls": 0},
        layout_meta={"viewport_w": 1280, "viewport_h": 800, "doc_height": 1400},
        cta_inventory=[],
        forms_inventory=[],
        images_inventory=[],
        css_palette=[],
        fonts=[],
        schema_blocks=[
            {"__format": "json-ld", "@type": "Product"},
            {"__format": "microdata", "@type": "https://schema.org/BreadcrumbList"},
        ],
    )

    formatted = studio._format_extract_for_llm(extract)

    assert "лабораторная скорость одного браузерного рендера" in formatted
    assert "Schema.org блоки (json-ld, microdata)" in formatted
    assert "Product" in formatted
    assert "https://schema.org/BreadcrumbList" in formatted
