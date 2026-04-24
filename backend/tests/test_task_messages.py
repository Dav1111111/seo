from app.collectors.tasks import _format_webmaster_result
from app.core_audit.priority.tasks import _format_priority_rescore_message


def test_format_webmaster_empty_window_message():
    message, extra, terminal_status = _format_webmaster_result({
        "queries": 0,
        "metrics": 0,
        "indexing": 0,
        "window_start": "2026-03-15",
        "window_end": "2026-04-13",
    })

    assert terminal_status == "done"
    assert "Яндекс не вернул новых данных" in message
    assert "2026-03-15" in message
    assert extra["empty_window"] is True


def test_format_webmaster_host_not_loaded_message():
    message, extra, terminal_status = _format_webmaster_result({
        "status": "host_not_loaded",
        "host_id": "https:example.ru:443",
        "window_start": "2026-03-15",
        "window_end": "2026-04-13",
    })

    assert terminal_status == "skipped"
    assert "хост ещё не загружен" in message
    assert extra["host_id"] == "https:example.ru:443"


def test_format_priority_rescore_message_includes_counts():
    message, extra = _format_priority_rescore_message({
        "scored": 12,
        "dropped": 3,
        "zeroed_older": 5,
    })

    assert "12" in message
    assert "3" in message
    assert "5" in message
    assert extra == {"scored": 12, "dropped": 3, "zeroed_older": 5}
