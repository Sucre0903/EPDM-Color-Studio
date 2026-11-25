"""Regression through the V14 Streamlit entry point in a disposable app directory."""
from io import BytesIO
from pathlib import Path
import os
import shutil
import sys
import tempfile
from xml.etree import ElementTree

from openpyxl import load_workbook
from PIL import Image
from streamlit.testing.v1 import AppTest

APP_FILES = (
    "app.py", "branding.py", "color_utils.py", "database.py",
    "excel_export.py", "ratio_search.py", "sample_preview.py",
    "data", "assets", ".streamlit",
)


def button(app, label):
    return next(b for b in app.button if b.label.startswith(label))


def verify(test_root):
    sys.path.insert(0, str(test_root))
    import database
    from excel_export import build_recipe_workbook
    from ratio_search import load_library

    app = AppTest.from_file(str(test_root / "app.py"), default_timeout=120).run()
    assert not app.exception, app.exception
    assert {"工作台", "手动配色", "色库", "历史记录"} <= {tab.label for tab in app.tabs}
    assert len(load_library()) == 40
    assert not database.get_records()

    app.checkbox(key="override_t2").check().run()
    app.radio(key="explore_mode_t2").set_value("方案探索（Explore）")
    app.multiselect(key="locked_t2").set_value(["FK-033"])
    app.multiselect(key="excluded_t2").set_value(["FK-031"])
    app.slider(key="accent_pct_t2").set_value(4.0)
    button(app, "生成配比").click().run()
    assert not app.exception, app.exception
    assert not app.session_state["batch_errors"], app.session_state["batch_errors"]
    batch = app.session_state["batch_res"]
    assert len(batch) == 2
    best, explore = [item["res"] for item in batch]
    assert len(best["explore"]) == 1
    assert len(explore["explore"]) > 1
    assert explore["used_accent"] and explore["accent_code"] != "FK-031"
    for plan in explore["explore"]:
        codes = {c["色号"] for c in plan["components"]}
        assert "FK-033" in codes and "FK-031" not in codes
        assert abs(sum(c["比例"] for c in plan["components"]) - 100) < 0.01

    workbook = build_recipe_workbook([
        {
            "formula_id": item["formula_id"], "name": item["name"],
            "target_hex": item["res"]["target_hex"], "material": item["res"]["material"],
            **item["res"]["recommended"],
        }
        for item in batch
    ])
    sheet = load_workbook(BytesIO(workbook)).active
    rows = list(sheet.values)
    assert {"R1", "R2"}.issubset({row[0] for row in rows})
    assert sheet["C2"].fill.patternType == "solid"

    button(app, "⭐ 收藏全部到历史记录").click().run()
    assert not app.exception, app.exception
    assert len(database.get_records()) == 2
    app.selectbox(key="copy_source_select").select_index(1).run()
    button(app, "复制过来").click().run()
    manual = app.session_state["manual_items"][0]
    mid = manual["_id"]
    assert app.text_input(key=f"mtarget_text_{mid}").value == best["target_hex"]
    app.button(key=f"mgen_{mid}").click().run()
    assert not app.exception, app.exception
    result = app.session_state["manual_items"][0]["result"]
    assert result["delta_e"] is not None
    downloads = [item.proto.label for item in app.get("download_button")]
    assert any("Excel" in label for label in downloads)
    assert any("PNG" in label for label in downloads)
    assert any("SVG" in label for label in downloads)

    # Exercise the application-level cached renderers with identical arguments twice.
    import app as app_module
    plan = best["recommended"]
    components = app_module._components_key(plan["components"])
    params = (plan["mixed_hex"], best["target_hex"], components, best["material"],
              plan["delta_e"], None, (), 8, 42)
    app_module.cached_png_bytes.clear()
    png = app_module.cached_png_bytes(*params)
    assert png == app_module.cached_png_bytes(*params)
    with Image.open(BytesIO(png)) as rendered:
        rendered.verify()
        assert min(rendered.size) > 200
    svg = app_module.cached_svg_str(*params)
    assert svg == app_module.cached_svg_str(*params)
    assert ElementTree.fromstring(svg).tag.endswith("}svg")
    print("V14 application regression passed")


def main():
    source = Path(sys.argv[1]).resolve()
    old_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="zt-regression-") as tmp:
        root = Path(tmp)
        for name in APP_FILES:
            path = source / name
            if path.is_dir():
                shutil.copytree(path, root / name, ignore=shutil.ignore_patterns("*.db"))
            else:
                shutil.copy2(path, root / name)
        try:
            os.chdir(root)
            verify(root)
        finally:
            os.chdir(old_cwd)


if __name__ == "__main__":
    main()
