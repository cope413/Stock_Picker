"""landry brief and landry usage: smoke tests on the live workbook and a synthetic transcript."""
import json
import glob

from landry import brief, usage


def _wb():
    return sorted(glob.glob("LANDRY_SYSTEM_WORKBOOK_25.xlsx"))[0]


def test_brief_is_compact_and_has_sections():
    t = brief.build(_wb())
    assert "JOURNAL:" in t and "OPEN ITEMS:" in t and "PROCESS CHECKLIST:" in t and "GIT:" in t
    assert len(t) < 12000


def test_usage_reports_thinking_and_compactions(tmp_path):
    p = tmp_path / "s.jsonl"
    rows = []
    for k in range(4):
        rows.append({"type": "assistant", "message": {"id": f"m{k}", "usage": {"input_tokens": 1, "cache_read_input_tokens": 1000 * (k + 1),
                     "cache_creation_input_tokens": 10, "output_tokens": 500},
                     "content": [{"type": "text", "text": "x" * 36}]}})
        rows.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": "y" * 360}]}})
        if k == 1:
            rows.append({"type": "system", "subtype": "compact_boundary"})
    p.write_text("\n".join(json.dumps(r) for r in rows))
    calls = usage.parse(str(p))
    assert len(calls) == 4 and calls[-1]["seg"] == 1
    r = usage.report(calls)
    assert "compactions 1" in r and "thinking" in r
