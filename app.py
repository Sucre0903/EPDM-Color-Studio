"""
ZT Color Match Studio —— EPDM Granule Mix Designer
====================================================
一键启动请双击 start.bat；或命令行 streamlit run app.py

从「配比计算程序」升级为「数字材料设计工作台」的 EPDM 点彩混色工具，
包含两条互补的工作流：

- **自动解析（工作台）**：批量输入目标色（可自定义命名，每个目标色可
  单独设置锁定/排除颜色和点缀杂色，也可以直接沿用全局默认设置）→ 点彩
  混合最优化求配比 → 材料样板预览 → 批量导出 Excel 配方表 / 单张 SVG/PNG。
- **手动配色**：从头指定色号+比例，或从自动解析结果里一键复制过来做
  微调，实时预览，导出方式和自动解析完全一致——满足经验配色、来料配色
  这类不需要（或不信任）算法自动求解的实际工作场景。

性能设计：材料预览图（PNG）和 SVG 的渲染都做了 `st.cache_data` 缓存，
缓存 key 是「混合色 + 目标色 + 配比 + 材料 + 颗粒尺度 + 随机种子」这些
真正影响画面的参数——切换方案单选、下载文件、调整其它无关控件触发的
页面重跑，只要这些参数没变就直接命中缓存，不会重新跑一遍渲染，避免
目标色一多就白屏卡顿。
"""

import os
import re
import random
import tempfile
import pandas as pd
import streamlit as st

from ratio_search import (
    search_recipe, load_library, import_library_table,
    DEFAULT_ACCENT_PCT, DEFAULT_FLOOR_PCT,
)
from sample_preview import draw_sample, build_svg, DEFAULT_GRAIN_PX
from branding import (
    INK, INK_SOFT, INK_FAINT, PAPER, CARD, CARD_BORDER, ACCENT,
    FONT_STACK, BRAND_NAME, BRAND_SUB, logo_mark_html,
)
from excel_export import build_recipe_workbook
import database as db

st.set_page_config(page_title=f"{BRAND_NAME} · {BRAND_SUB}",
                   page_icon="◤", layout="wide")

DATA_CSV = "data/fk_colors.csv"
MATERIALS = ["EPDM", "PP", "PE", "ABS", "PVC", "PC", "PS", "PA", "TPU", "TPE"]
HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")
# 重量(kg) 已经从界面和所有导出结果中拿掉——比例 + 总产量本就是同一件事，
# 这里给算法内部传一个固定占位值就够了，用户不会在任何地方看到它。
FIXED_TOTAL_WEIGHT = 100.0


# =========================================================================
# 缓存层：把「真正耗时」的计算（最优化求解 / 颗粒质感渲染 / SVG 生成）用
# st.cache_data 包起来，key 只取真正影响结果的参数。这样调整颗粒尺度滑块、
# 切换方案对比、点下载按钮等触发的页面重跑，只要这些参数没变就直接命中
# 缓存，不会重新计算——这是这一版解决「操作卡顿」问题的核心机制。
# =========================================================================

def _csv_mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def cached_search_recipe(target_hex, total_weight, material, n_colors, use_accent,
                         accent_pct, locked_tuple, excluded_tuple, explore_mode,
                         csv_path, _mtime):
    return search_recipe(target_hex, total_weight, material, n_colors=n_colors,
                         use_accent=use_accent, accent_pct=accent_pct,
                         locked_codes=list(locked_tuple),
                         excluded_codes=list(excluded_tuple),
                         explore_mode=explore_mode, csv_path=csv_path)


@st.cache_data(show_spinner=False)
def cached_png_bytes(mixed_hex, target_hex, components_tuple, material, delta_e,
                     accent_code, locked_tuple, avg_grain_px, seed):
    components = [{"色号": a, "HEX": b, "比例": c} for a, b, c in components_tuple]
    tmp_path = tempfile.NamedTemporaryFile(delete=False, suffix=".png").name
    try:
        draw_sample(mixed_hex, target_hex, components, material=material,
                   delta_e=delta_e, accent_code=accent_code,
                   locked_codes=list(locked_tuple), avg_grain_px=avg_grain_px,
                   seed=seed, out_path=tmp_path)
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@st.cache_data(show_spinner=False)
def cached_svg_str(mixed_hex, target_hex, components_tuple, material, delta_e,
                   accent_code, locked_tuple, avg_grain_px, seed):
    components = [{"色号": a, "HEX": b, "比例": c} for a, b, c in components_tuple]
    return build_svg(mixed_hex, target_hex, components, material=material,
                     delta_e=delta_e, accent_code=accent_code,
                     locked_codes=list(locked_tuple), avg_grain_px=avg_grain_px, seed=seed)


def _components_key(components):
    return tuple((c["色号"], c["HEX"], c["比例"]) for c in components)


def render_material_card(plan, target_hex, material, seed, avg_grain_px,
                         accent_code, locked_codes, key_prefix):
    """渲染一张材料样板卡片需要的 (png_bytes, svg_str)，统一走缓存。"""
    comps_key = _components_key(plan["components"])
    locked_key = tuple(sorted(locked_codes or []))
    png_bytes = cached_png_bytes(plan["mixed_hex"], target_hex, comps_key, material,
                                 plan["delta_e"], accent_code, locked_key,
                                 avg_grain_px, seed)
    svg_str = cached_svg_str(plan["mixed_hex"], target_hex, comps_key, material,
                             plan["delta_e"], accent_code, locked_key,
                             avg_grain_px, seed)
    return png_bytes, svg_str


# ---------- 全局样式：中性克制的设计工具视觉语言 ----------

def inject_css():
    css_path = os.path.join(os.path.dirname(__file__), "assets", "inter.css")
    with open(css_path, encoding="utf-8") as font_file:
        local_font_css = font_file.read()
    st.markdown(f"""
    <style>
    {local_font_css}

    html, body, [class*="css"] {{
        font-family: {FONT_STACK};
    }}

    [data-testid="stHeader"] {{ display: none; }}
    [data-testid="stDecoration"] {{ display: none; }}
    #MainMenu {{ visibility: hidden; }}
    footer {{ visibility: hidden; }}

    .block-container {{
        padding-top: 2.2rem;
        max-width: 1600px;
    }}

    .fkcs-header {{
        display: flex; align-items: center; gap: 14px;
        padding-bottom: 14px; margin-bottom: 18px;
        border-bottom: 1px solid {CARD_BORDER};
    }}
    .fkcs-header .fkcs-brand {{
        font-size: 19px; font-weight: 700; color: {INK}; letter-spacing: -.2px;
    }}
    .fkcs-header .fkcs-sub {{
        font-size: 11px; font-weight: 500; color: {INK_FAINT};
        letter-spacing: 1.2px; margin-top: 1px;
    }}

    .fkcs-label {{
        font-size: 12px; font-weight: 600; color: {INK_FAINT};
        letter-spacing: 1px; text-transform: uppercase;
        margin: 4px 0 8px 0;
    }}

    [data-testid="column"] {{ padding: 0 16px; }}

    .fkcs-target-card {{
        border: 1px solid {CARD_BORDER}; border-radius: 10px;
        padding: 10px 14px 4px 14px; margin-bottom: 10px; background: {CARD};
    }}

    .fkcs-batch-title {{ font-size: 15px; font-weight: 700; color: {INK}; }}
    .fkcs-batch-sub {{ font-size: 12px; color: {INK_FAINT}; margin-left: 8px; font-weight: 400; }}

    .fkcs-palette {{ display: flex; flex-direction: column; gap: 10px; }}
    .fkcs-palette-row {{
        display: flex; align-items: center; gap: 12px;
        background: {CARD}; border: 1px solid {CARD_BORDER};
        border-radius: 10px; padding: 9px 12px;
    }}
    .fkcs-swatch {{
        width: 34px; height: 34px; border-radius: 8px; flex-shrink: 0;
        border: 1px solid rgba(0,0,0,0.08);
    }}
    .fkcs-palette-main {{ min-width: 92px; }}
    .fkcs-palette-code {{ font-size: 13.5px; font-weight: 600; color: {INK}; }}
    .fkcs-palette-hex {{ font-size: 11.5px; color: {INK_FAINT}; font-family: monospace; }}
    .fkcs-palette-bar-wrap {{
        flex: 1; height: 6px; background: {PAPER}; border-radius: 3px;
        overflow: hidden; min-width: 40px;
    }}
    .fkcs-palette-bar {{ height: 100%; border-radius: 3px; }}
    .fkcs-palette-pct {{
        font-size: 13px; font-weight: 600; color: {INK}; width: 48px; text-align: right;
    }}
    .fkcs-tag {{
        display: inline-block; font-size: 10px; font-weight: 600;
        color: {INK_SOFT}; background: {PAPER}; border: 1px solid {CARD_BORDER};
        border-radius: 4px; padding: 1px 5px; margin-left: 6px;
    }}

    .fkcs-compare {{ display: flex; gap: 14px; margin-bottom: 6px; }}
    .fkcs-compare-item {{ flex: 1; }}
    .fkcs-compare-swatch {{
        width: 100%; height: 56px; border-radius: 10px;
        border: 1px solid rgba(0,0,0,0.08); margin-bottom: 6px;
    }}
    .fkcs-compare-label {{ font-size: 11px; color: {INK_FAINT}; }}
    .fkcs-compare-hex {{ font-size: 13px; font-weight: 600; color: {INK}; font-family: monospace; }}

    .fkcs-empty-canvas {{
        display: flex; align-items: center; justify-content: center;
        height: 420px; border: 1.5px dashed {CARD_BORDER}; border-radius: 14px;
        color: {INK_FAINT}; font-size: 14px; text-align: center; line-height: 1.8;
    }}
    </style>
    """, unsafe_allow_html=True)


def render_header():
    st.markdown(
        f'<div class="fkcs-header">{logo_mark_html(30)}'
        f'<div><div class="fkcs-brand">{BRAND_NAME}</div>'
        f'<div class="fkcs-sub">{BRAND_SUB.upper()}</div></div></div>',
        unsafe_allow_html=True,
    )


inject_css()
render_header()


# ---------- 小工具 ----------

def show_image(data):
    """兼容新旧 Streamlit 版本：新版本用 use_container_width，
    旧版本（这个参数还不存在）退回 use_column_width。"""
    try:
        st.image(data, use_container_width=True)
    except TypeError:
        st.image(data, use_column_width=True)


def swatch_html(hex_color, size=22):
    return (f"<span style='display:inline-block;width:{size}px;height:{size}px;"
            f"background:{hex_color};border:1px solid #999;border-radius:4px;"
            f"vertical-align:middle;'></span>")


def norm_hex(h):
    h = str(h).strip()
    if not h.startswith("#"):
        h = "#" + h
    return h.upper()


def plan_to_df(plan, accent_code=None, locked_codes=None):
    locked_codes = locked_codes or []
    rows = []
    for c in plan["components"]:
        tags = []
        if c["色号"] == accent_code:
            tags.append("点缀")
        if c["色号"] in locked_codes:
            tags.append("锁定")
        tag = f" ⭐{'/'.join(tags)}" if tags else ""
        rows.append({
            "色号": c["色号"] + tag, "HEX": c.get("HEX", ""),
            "比例": f"{c['比例']}%",
        })
    return pd.DataFrame(rows)


def render_palette_html(components, accent_code=None, locked_codes=None):
    locked_codes = locked_codes or []
    rows = []
    for c in components:
        tags = []
        if c["色号"] == accent_code:
            tags.append("点缀")
        if c["色号"] in locked_codes:
            tags.append("锁定")
        tag_html = "".join(f'<span class="fkcs-tag">{t}</span>' for t in tags)
        rows.append(f'''
        <div class="fkcs-palette-row">
          <div class="fkcs-swatch" style="background:{c['HEX']}"></div>
          <div class="fkcs-palette-main">
            <div class="fkcs-palette-code">{c['色号']}{tag_html}</div>
            <div class="fkcs-palette-hex">{c['HEX'].upper()}</div>
          </div>
          <div class="fkcs-palette-bar-wrap">
            <div class="fkcs-palette-bar" style="width:{c['比例']}%;background:{c['HEX']}"></div>
          </div>
          <div class="fkcs-palette-pct">{c['比例']}%</div>
        </div>''')
    return '<div class="fkcs-palette">' + "".join(rows) + '</div>'


def render_compare_html(target_hex, mixed_hex):
    return f'''
    <div class="fkcs-compare">
      <div class="fkcs-compare-item">
        <div class="fkcs-compare-swatch" style="background:{target_hex}"></div>
        <div class="fkcs-compare-label">目标色</div>
        <div class="fkcs-compare-hex">{target_hex.upper()}</div>
      </div>
      <div class="fkcs-compare-item">
        <div class="fkcs-compare-swatch" style="background:{mixed_hex}"></div>
        <div class="fkcs-compare-label">感知混合色</div>
        <div class="fkcs-compare-hex">{mixed_hex.upper()}</div>
      </div>
    </div>'''


def plan_options_for(res):
    """
    Best Match 模式：res["explore"] 只有 1 项，返回 {"推荐方案": ...}。
    Explore 模式：res["explore"] 有多项（第一项是最优解），返回
    {"推荐方案": ..., "方案 2": ..., "方案 3": ..., ...}。
    """
    explore = res.get("explore") or [res["recommended"]]
    opts = {}
    for i, plan in enumerate(explore):
        label = "推荐方案" if i == 0 else f"方案 {i + 1}"
        opts[label] = plan
    return opts


def next_target_id():
    st.session_state["target_id_seq"] = st.session_state.get("target_id_seq", 0) + 1
    return f"t{st.session_state['target_id_seq']}"


def new_target(name, hexval):
    return {
        "_id": next_target_id(), "name": name, "hex": hexval,
        "override": False, "locked": [], "excluded": [],
        "use_accent": None, "accent_pct": None, "n_colors": None,
        "explore_mode": None,
    }


def partitive_mix_hex(components):
    """按点彩式光学混合（线性光空间面积占比加权平均）算出实际混合色，
    和自动解析模块用的物理模型保持一致，而不是简单的 RGB 平均。"""
    import numpy as np
    from color_utils import hex_to_rgb, rgb_to_hex, rgb_to_linear01, _linear_to_srgb01
    total = sum(c["比例"] for c in components)
    if total <= 0:
        return "#808080"
    lin_sum = np.zeros(3)
    for c in components:
        w = c["比例"] / total
        lin_sum += w * rgb_to_linear01(hex_to_rgb(c["HEX"]))
    srgb = np.clip(_linear_to_srgb01(lin_sum), 0, 1) * 255.0
    return rgb_to_hex(srgb)


def normalize_manual_components(raw_pairs, lib_df):
    """raw_pairs: [(色号, 比例字符串), ...]。色号必须在色库里；比例不需要
    凑够 100，会自动按比例归一化，再用和自动解析同一套取整逻辑四舍五入。
    返回 (components, errors)。"""
    from ratio_search import _round_to_100
    lib_map = dict(zip(lib_df["色号"], lib_df["HEX"]))
    comps, errors = [], []
    for i, (code, pct_str) in enumerate(raw_pairs):
        code = code.strip()
        if not code:
            continue
        if code not in lib_map:
            errors.append(f"第{i+1}行：色号「{code}」不在色库里")
            continue
        try:
            pct = float(pct_str)
        except (TypeError, ValueError):
            errors.append(f"第{i+1}行：比例「{pct_str}」不是有效数字")
            continue
        if pct <= 0:
            continue
        comps.append({"色号": code, "HEX": lib_map[code], "比例": pct})
    if not comps:
        return [], errors
    total = sum(c["比例"] for c in comps)
    if abs(total - 100) > 0.01:
        for c in comps:
            c["比例"] = c["比例"] / total * 100
    codes = [c["色号"] for c in comps]
    hexes = [c["HEX"] for c in comps]
    pcts = [c["比例"] for c in comps]
    rounded = _round_to_100(codes, hexes, pcts)
    return rounded, errors


def manual_delta_e(target_hex, mixed_hex):
    from color_utils import hex_to_lab, delta_e_ciede2000
    return round(float(delta_e_ciede2000(hex_to_lab(target_hex), hex_to_lab(mixed_hex))), 2)


def delta_e_quality_badge(de):
    """ΔE00 数值 -> 分档小标签（优秀/可接受/建议调整），配色一眼看出档位。"""
    if de is None:
        return ""
    if de < 3:
        label, color = "优秀 Excellent", "#2E7D32"
    elif de < 6:
        label, color = "可接受 Acceptable", "#B8860B"
    else:
        label, color = "建议调整 Needs Adjustment", "#C0392B"
    return (f'<span style="background:{color}1A;color:{color};'
           f'border:1px solid {color}55;border-radius:6px;padding:3px 10px;'
           f'font-size:13px;font-weight:600;display:inline-block;">'
           f'ΔE00 {de:.2f} · {label}</span>')


# ---------- 页面 ----------

tab_gen, tab_manual, tab_lib, tab_hist = st.tabs(["工作台", "手动配色", "色库", "历史记录"])

# ===== Tab 1：工作台（自动解析：左参数 / 右批量结果列表）=====
with tab_gen:
    lib_df = load_library(DATA_CSV)
    code_label = {}
    for _, row in lib_df.iterrows():
        label = row["色号"] + (f"（{row['名称']}）" if row["名称"] else "")
        code_label[label] = row["色号"]
    all_codes_sorted = sorted(lib_df["色号"].tolist())

    if "targets" not in st.session_state:
        st.session_state["targets"] = [
            new_target("1", "#07166B"), new_target("2", "#EC7539"),
        ]

    col_left, col_results = st.columns([1.05, 2.55])

    # ---------- 左栏：目标色 + 参数 ----------
    with col_left:
        st.markdown('<div class="fkcs-label">快速添加目标色</div>', unsafe_allow_html=True)
        if "quick_picker" not in st.session_state:
            st.session_state["quick_picker"] = "#1E3A8C"
        if "quick_hex_text" not in st.session_state:
            st.session_state["quick_hex_text"] = st.session_state["quick_picker"]

        def _sync_from_picker():
            st.session_state["quick_hex_text"] = st.session_state["quick_picker"]

        def _sync_from_text():
            v = str(st.session_state["quick_hex_text"]).strip()
            if not v.startswith("#"):
                v = "#" + v
            if HEX_RE.match(v):
                st.session_state["quick_picker"] = v.upper()

        qc1, qc2 = st.columns([1, 2.4])
        with qc1:
            st.color_picker("取色器", key="quick_picker", on_change=_sync_from_picker,
                           label_visibility="collapsed")
        with qc2:
            st.text_input("HEX", key="quick_hex_text", on_change=_sync_from_text,
                         label_visibility="collapsed", placeholder="#RRGGBB")
        quick_name = st.text_input("名称（可选）", value="", key="quick_name_input",
                                   placeholder="不填自动编号", label_visibility="collapsed")
        if st.button("+ 添加到目标色列表", use_container_width=True, key="quick_add_btn"):
            h = norm_hex(st.session_state["quick_hex_text"])
            if HEX_RE.match(h):
                nm = quick_name.strip() or str(len(st.session_state["targets"]) + 1)
                st.session_state["targets"].append(new_target(nm, h))
            else:
                st.error("取色器 HEX 不合法，没能添加。")

        show_bulk = st.checkbox("批量粘贴添加（从 Excel 粘贴多行）", key="show_bulk_paste")
        if show_bulk:
            st.caption("每行一个目标色，格式「名称,HEX」，例如：\n深海蓝,#07166B\n橘红,#EC7539\n"
                      "（也支持从 Excel 粘贴两列，用制表符分隔）")
            bulk_text = st.text_area("批量粘贴", key="bulk_paste_text", height=110,
                                     label_visibility="collapsed")
            if st.button("解析并追加", key="bulk_add_btn"):
                added, bad = 0, []
                for i, line in enumerate(bulk_text.strip().split("\n")):
                    line = line.strip()
                    if not line:
                        continue
                    parts = re.split(r"[,，\t]+", line)
                    if len(parts) < 2:
                        bad.append(f"第{i+1}行格式不对：{line}")
                        continue
                    nm, hx = parts[0].strip(), norm_hex(parts[1])
                    if not HEX_RE.match(hx):
                        bad.append(f"第{i+1}行 HEX 不合法：{line}")
                        continue
                    st.session_state["targets"].append(new_target(nm or str(i + 1), hx))
                    added += 1
                if added:
                    st.success(f"已追加 {added} 个目标色。")
                for msg in bad:
                    st.warning(msg)

        st.markdown('<div class="fkcs-label" style="margin-top:14px">目标色列表</div>',
                   unsafe_allow_html=True)
        st.caption("每个目标色可以单独勾选「独立设置高级参数」，不勾选就沿用下面的全局默认设置。")

        remove_id = None
        for t in st.session_state["targets"]:
            tid = t["_id"]
            st.markdown('<div class="fkcs-target-card">', unsafe_allow_html=True)
            rc1, rc2, rc3 = st.columns([1.3, 1.6, 0.5])
            with rc1:
                t["name"] = st.text_input("名称", value=t["name"], key=f"name_{tid}",
                                          label_visibility="collapsed")
            with rc2:
                t["hex"] = st.text_input("目标色 HEX", value=t["hex"], key=f"hex_{tid}",
                                         label_visibility="collapsed")
            with rc3:
                if st.button("✕", key=f"del_{tid}", use_container_width=True):
                    remove_id = tid
            hv = norm_hex(t["hex"])
            if HEX_RE.match(hv):
                st.markdown(swatch_html(hv, 16) + f" {hv}", unsafe_allow_html=True)
            else:
                st.caption("HEX 格式看起来不太对，应为 #RRGGBB")

            t["override"] = st.checkbox("独立设置高级参数（不勾选则使用下方全局默认）",
                                        value=t["override"], key=f"override_{tid}")
            if t["override"]:
                t["n_colors"] = st.slider(
                    "本色颜色数量", 2, 6, t["n_colors"] or 4, key=f"ncolors_{tid}",
                    help="这个目标色单独使用的颜色数量，不受下方全局设置影响。",
                )
                locked_labels_i = st.multiselect(
                    "本色锁定颜色", options=list(code_label.keys()), key=f"locked_{tid}",
                    default=[l for l in code_label if code_label[l] in t["locked"]],
                )
                t["locked"] = [code_label[l] for l in locked_labels_i]
                excluded_labels_i = st.multiselect(
                    "本色排除颜色（不参与本色搜索）", options=list(code_label.keys()),
                    key=f"excluded_{tid}",
                    default=[l for l in code_label if code_label[l] in t["excluded"]],
                )
                t["excluded"] = [code_label[l] for l in excluded_labels_i]
                use_accent_i = st.checkbox("加入点缀杂色", value=True, key=f"accent_on_{tid}")
                t["use_accent"] = use_accent_i
                t["accent_pct"] = st.slider("点缀杂色比例 (%)", 2.0, 15.0,
                                            DEFAULT_ACCENT_PCT, step=1.0,
                                            key=f"accent_pct_{tid}", disabled=not use_accent_i)
                explore_i = st.radio(
                    "本色配方模式", ["推荐方案（Best Match）", "方案探索（Explore）"],
                    key=f"explore_mode_{tid}", horizontal=True,
                    help="推荐方案：只给 1 个最接近目标色的最优方案；"
                        "方案探索：在色差可接受范围内，给最多 6 个配色结构"
                        "有明显区别的方案（不同颜色组合，不只是调亮度）。",
                )
                t["explore_mode"] = (explore_i == "方案探索（Explore）")
            st.markdown('</div>', unsafe_allow_html=True)

        if remove_id is not None:
            st.session_state["targets"] = [t for t in st.session_state["targets"]
                                           if t["_id"] != remove_id]

        st.markdown('<div class="fkcs-label" style="margin-top:6px">基础参数</div>',
                   unsafe_allow_html=True)
        material = st.selectbox("材料", MATERIALS, index=0)
        n_colors = st.slider("颜色数量（全局默认）", 2, 6, 4,
                             help="没有勾选「独立设置高级参数」的目标色，统一使用这个"
                                 "颜色数量；勾选了独立设置的目标色，用它自己卡片里的"
                                 "「本色颜色数量」。")

        st.markdown('<div class="fkcs-label" style="margin-top:6px">颗粒视觉尺度</div>',
                   unsafe_allow_html=True)
        grain_px = st.slider(
            "颗粒尺度", 2, 40, DEFAULT_GRAIN_PX, step=1, key="grain_px_widget",
            help="越小=颗粒越细，模拟远距离观看铺装完成面的综合色彩效果；"
                "越大=颗粒越粗，模拟近距离观察单颗粒细节。",
            label_visibility="collapsed",
        )
        _applied_grain = st.session_state.get("applied_grain_px")
        if _applied_grain is not None and _applied_grain != grain_px:
            st.caption(f"已调整为 {grain_px}px，点击下方「生成配比」后应用"
                      f"（当前预览仍是 {_applied_grain}px）")

        with st.expander("全局默认高级设置", expanded=False):
            st.caption("这里是「没有勾选独立设置」的目标色统一使用的默认值。")

            st.markdown("**配方模式**")
            explore_default_choice = st.radio(
                "配方模式", ["推荐方案（Best Match）", "方案探索（Explore）"],
                key="global_explore_mode", horizontal=True, label_visibility="collapsed",
                help="推荐方案：只给 1 个最接近目标色的最优方案，适合日常使用；"
                    "方案探索：在色差可接受范围内，给最多 6 个配色结构有明显"
                    "区别的方案（不同颜色组合/数量/主次比例），适合方案比选。"
                    "两种模式生成的方案功能完全一样——都能选中、预览、导出、"
                    "收藏、复制到手动配色。",
            )
            explore_mode_default = (explore_default_choice == "方案探索（Explore）")

            st.markdown("**锁定必用颜色**")
            locked_labels = st.multiselect(
                "指定必须出现在配比里的颜色（品牌色/库存色），最多 8 个",
                options=list(code_label.keys()), label_visibility="collapsed",
                key="global_locked",
            )
            locked_codes_input = [code_label[l] for l in locked_labels]

            st.markdown("**排除颜色**")
            excluded_labels = st.multiselect(
                "本次搜索完全不考虑这些颜色（如临时缺货）",
                options=list(code_label.keys()), label_visibility="collapsed",
                key="global_excluded",
            )
            excluded_codes_input = [code_label[l] for l in excluded_labels]

            st.markdown("**点缀杂色**")
            use_accent = st.checkbox(
                "加入点缀杂色（更接近真实 EPDM 铺装的跳色质感）", value=True,
                help="目标偏暗时自动配浅色点缀，目标偏亮时配深色点缀。"
                    "仅在颜色数量 ≥3 时生效。", key="global_use_accent",
            )
            accent_pct = st.slider("点缀杂色比例 (%)", 2.0, 15.0,
                                   DEFAULT_ACCENT_PCT, step=1.0,
                                   disabled=not use_accent, key="global_accent_pct")
            st.caption("点缀比例越高，整体观感偏离目标色越明显——这是真实的"
                      "光学混合效应，不是拟合误差，可按需要调节取舍。")

        gen = st.button("生成配比", type="primary", use_container_width=True)

        if gen:
            gen_errors = []
            batch_targets = []
            for i, t in enumerate(st.session_state["targets"]):
                name = t["name"].strip() or str(i + 1)
                h = norm_hex(t["hex"])
                if not HEX_RE.match(h):
                    gen_errors.append(f"「{name}」的 HEX（{t['hex']}）格式不对，已跳过")
                    continue
                if t["override"]:
                    eff = {
                        "n_colors": t["n_colors"] or 4,
                        "locked": t["locked"], "excluded": t["excluded"],
                        "use_accent": t["use_accent"], "accent_pct": t["accent_pct"],
                        "explore_mode": t["explore_mode"] if t["explore_mode"] is not None else explore_mode_default,
                    }
                else:
                    eff = {
                        "n_colors": n_colors,
                        "locked": locked_codes_input, "excluded": excluded_codes_input,
                        "use_accent": use_accent, "accent_pct": accent_pct,
                        "explore_mode": explore_mode_default,
                    }
                batch_targets.append((name, h, eff))

            if not batch_targets:
                st.error("目标色列表里没有可用的行，请检查 HEX 格式（应为 #RRGGBB）。")
            else:
                batch_res = []
                mtime = _csv_mtime(DATA_CSV)
                for i, (name, hexval, eff) in enumerate(batch_targets):
                    formula_id = f"R{i + 1}"
                    try:
                        res = cached_search_recipe(
                            hexval, FIXED_TOTAL_WEIGHT, material, eff["n_colors"],
                            eff["use_accent"], eff["accent_pct"],
                            tuple(eff["locked"]), tuple(eff["excluded"]),
                            eff["explore_mode"], DATA_CSV, mtime,
                        )
                        batch_res.append({"formula_id": formula_id, "name": name, "res": res})
                    except Exception as e:
                        gen_errors.append(f"「{name}」（{hexval}）计算失败：{e}")

                st.session_state["batch_res"] = batch_res
                st.session_state["batch_errors"] = gen_errors
                st.session_state["batch_seeds"] = {
                    item["formula_id"]: random.randint(0, 2**31 - 1) for item in batch_res
                }
                st.session_state["applied_grain_px"] = grain_px

    batch_res = st.session_state.get("batch_res")

    # ---------- 右栏：批量结果列表 ----------
    with col_results:
        st.markdown('<div class="fkcs-label">批量结果</div>', unsafe_allow_html=True)

        for msg in st.session_state.get("batch_errors", []):
            st.warning(msg)

        if not batch_res:
            st.markdown(
                '<div class="fkcs-empty-canvas">在左侧填好目标色列表，点击「生成配比」<br>'
                '这里会按顺序显示每个目标色的材料样板和配色方案</div>',
                unsafe_allow_html=True,
            )
        else:
            applied_grain_px = st.session_state.get("applied_grain_px", DEFAULT_GRAIN_PX)
            seeds = st.session_state.get("batch_seeds", {})

            action_c1, action_c2 = st.columns(2)
            with action_c1:
                export_items = []
                for item in batch_res:
                    fid = item["formula_id"]
                    res = item["res"]
                    choice = st.session_state.get(f"plan_choice_{fid}", "推荐方案")
                    plan = plan_options_for(res).get(choice, res["recommended"])
                    export_items.append({
                        "formula_id": fid, "name": item["name"],
                        "target_hex": res["target_hex"], "material": res["material"],
                        "delta_e": plan["delta_e"], "components": plan["components"],
                    })
                xlsx_bytes = build_recipe_workbook(export_items)
                st.download_button(
                    f"⬇ 下载全部配方 Excel（共 {len(batch_res)} 个）",
                    data=xlsx_bytes, file_name="EPDM配方表.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True,
                )
            with action_c2:
                if st.button(f"⭐ 收藏全部到历史记录（共 {len(batch_res)} 个）",
                            use_container_width=True):
                    saved = 0
                    for item in batch_res:
                        fid = item["formula_id"]
                        res = item["res"]
                        choice = st.session_state.get(f"plan_choice_{fid}", "推荐方案")
                        plan = plan_options_for(res).get(choice, res["recommended"])
                        db.save_record(
                            res["target_hex"], res["material"], FIXED_TOTAL_WEIGHT, plan,
                            note=f"⭐{item['name']}[{fid}]{res['n_colors']}色[{choice}]"
                            + (f"+点缀{res['accent_code']}" if res["used_accent"] else "")
                            + (f"+锁定{','.join(res['locked_codes'])}" if res["locked_codes"] else ""),
                        )
                        saved += 1
                    st.success(f"已收藏 {saved} 条到历史记录。")

            st.markdown("---")

            for idx, item in enumerate(batch_res):
                fid = item["formula_id"]
                name = item["name"]
                res = item["res"]
                for note in res.get("notes", []):
                    st.warning(f"「{name}」：{note}")

                plan_options = plan_options_for(res)

                with st.expander(f"{fid} · {name} · {res['target_hex']}",
                                 expanded=(idx == 0)):
                    choice = st.radio(
                        "方案", list(plan_options.keys()), horizontal=True,
                        key=f"plan_choice_{fid}", label_visibility="collapsed",
                    )
                    plan = plan_options[choice]
                    seed = seeds.get(fid)

                    png_bytes, svg_str = render_material_card(
                        plan, res["target_hex"], res["material"], seed,
                        applied_grain_px, res["accent_code"], res["locked_codes"], fid,
                    )

                    col_img, col_pal = st.columns([1.3, 1])
                    with col_img:
                        show_image(png_bytes)
                    with col_pal:
                        st.markdown(render_compare_html(res["target_hex"], plan["mixed_hex"]),
                                   unsafe_allow_html=True)
                        st.caption(f"ΔE00（点彩感知）= {plan['delta_e']}　·　{res['n_colors']} 色"
                                  + ("　·　含点缀" if res["used_accent"] else ""))
                        st.markdown(
                            render_palette_html(plan["components"], res["accent_code"],
                                                res["locked_codes"]),
                            unsafe_allow_html=True,
                        )
                        show_table = st.checkbox(
                            "显示数据表格 / 其它方案对比", key=f"show_table_{fid}",
                        )
                        if show_table:
                            st.dataframe(
                                plan_to_df(plan, res["accent_code"], res["locked_codes"]),
                                hide_index=True, use_container_width=True,
                            )
                            for pname, p in plan_options.items():
                                if pname == choice:
                                    continue
                                st.markdown(f"**{pname}**　ΔE00 = {p['delta_e']}")
                                st.markdown(
                                    render_palette_html(p["components"], res["accent_code"],
                                                        res["locked_codes"]),
                                    unsafe_allow_html=True,
                                )

                    c_dl1, c_dl2, c_dl3 = st.columns(3)
                    with c_dl1:
                        st.download_button(
                            "⬇ SVG", data=svg_str.encode("utf-8"),
                            file_name=f"{fid}_{name}_{plan['mixed_hex'].lstrip('#')}.svg",
                            mime="image/svg+xml", use_container_width=True,
                            key=f"dl_svg_{fid}",
                        )
                    with c_dl2:
                        st.download_button(
                            "⬇ PNG", data=png_bytes,
                            file_name=f"{fid}_{name}_{plan['mixed_hex'].lstrip('#')}.png",
                            mime="image/png", use_container_width=True,
                            key=f"dl_png_{fid}",
                        )
                    with c_dl3:
                        if st.button("⭐ 收藏", use_container_width=True, key=f"fav_{fid}"):
                            db.save_record(
                                res["target_hex"], res["material"], FIXED_TOTAL_WEIGHT, plan,
                                note=f"⭐{name}[{fid}]{res['n_colors']}色[{choice}]"
                                + (f"+点缀{res['accent_code']}" if res["used_accent"] else "")
                                + (f"+锁定{','.join(res['locked_codes'])}" if res["locked_codes"] else ""),
                            )
                            st.success(f"「{name}」已收藏到历史记录")

# ===== Tab 2：手动配色（Reverse Mix Workspace：色号+比例 → 实时预览）=====
with tab_manual:
    st.markdown('<div class="fkcs-label">手动配色 · Reverse Mix Workspace</div>',
               unsafe_allow_html=True)
    st.caption("适合经验配色、来料配色这类不需要（或不信任）算法自动求解的场景：直接"
              "指定色号和比例，实时看混色效果；也可以从「工作台」的解析结果一键复制"
              "过来做微调。比例不需要凑够 100，会自动按比例归一化。")

    lib_df_m = load_library(DATA_CSV)

    if "manual_items" not in st.session_state:
        st.session_state["manual_items"] = []

    src_c1, src_c2, src_c3 = st.columns([2.2, 0.9, 0.9])
    with src_c1:
        batch_res_m = st.session_state.get("batch_res")
        copy_options = {}
        if batch_res_m:
            for item in batch_res_m:
                fid = item["formula_id"]
                choice = st.session_state.get(f"plan_choice_{fid}", "推荐方案")
                plan_m = plan_options_for(item["res"]).get(choice, item["res"]["recommended"])
                copy_options[f"{fid} · {item['name']} · {item['res']['target_hex']}（{choice}）"] = (item, plan_m)
        picked_label = st.selectbox(
            "从工作台解析结果复制（可选）", ["（不选择）"] + list(copy_options.keys()),
            key="copy_source_select", label_visibility="collapsed",
        )
        if not batch_res_m:
            st.caption("工作台还没有生成过结果——可以直接「新建空白配方」。")
    with src_c2:
        copy_clicked = st.button("复制过来", use_container_width=True,
                                 disabled=(picked_label == "（不选择）"))
    with src_c3:
        blank_clicked = st.button("+ 新建空白", use_container_width=True)

    if copy_clicked and picked_label != "（不选择）":
        item, plan_m = copy_options[picked_label]
        raw_lines = "\n".join(f"{c['色号']},{c['比例']}" for c in plan_m["components"])
        new_mid = next_target_id()
        target_hex_src = item["res"]["target_hex"]
        # 复制自动解析结果时，顺手把原本的目标色带过来做参考——微调时
        # 天然就会想知道"改完之后离原来想要的颜色更近还是更远了"。
        st.session_state[f"mtarget_text_{new_mid}"] = target_hex_src
        st.session_state[f"mtarget_picker_{new_mid}"] = target_hex_src
        st.session_state["manual_items"].append({
            "_id": new_mid, "name": f"{item['name']}-微调",
            "material": item["res"]["material"], "raw_text": raw_lines,
            "result": None, "seed": None,
        })

    if blank_clicked:
        st.session_state["manual_items"].append({
            "_id": next_target_id(),
            "name": f"手动配方{len(st.session_state['manual_items']) + 1}",
            "material": "EPDM", "raw_text": "", "result": None, "seed": None,
        })

    if not st.session_state["manual_items"]:
        st.markdown(
            '<div class="fkcs-empty-canvas">还没有手动配方<br>'
            '点上面「复制过来」或「+ 新建空白」开始</div>',
            unsafe_allow_html=True,
        )

    remove_mid = None
    for mitem in st.session_state["manual_items"]:
        mid = mitem["_id"]
        with st.expander(mitem["name"], expanded=True):
            hc1, hc2, hc3 = st.columns([2, 1.2, 0.6])
            with hc1:
                mitem["name"] = st.text_input("名称", value=mitem["name"],
                                              key=f"mname_{mid}", label_visibility="collapsed")
            with hc2:
                mat_idx = MATERIALS.index(mitem["material"]) if mitem["material"] in MATERIALS else 0
                mitem["material"] = st.selectbox("材料", MATERIALS, index=mat_idx,
                                                 key=f"mmat_{mid}", label_visibility="collapsed")
            with hc3:
                if st.button("✕", key=f"mdel_{mid}", use_container_width=True):
                    remove_mid = mid

            st.caption("参考目标色（可选）——留空就是纯手动预览；填了就会算出和这个"
                      "颜色的 ΔE00 色差，帮你判断微调方向。")
            mt1, mt2 = st.columns([1, 3])

            def _sync_target_from_picker(mid=mid):
                st.session_state[f"mtarget_text_{mid}"] = st.session_state[f"mtarget_picker_{mid}"]

            def _sync_target_from_text(mid=mid):
                v = str(st.session_state[f"mtarget_text_{mid}"]).strip()
                if not v:
                    return
                if not v.startswith("#"):
                    v = "#" + v
                if HEX_RE.match(v):
                    st.session_state[f"mtarget_picker_{mid}"] = v.upper()

            with mt1:
                st.color_picker("取色器", value="#1E3A8C", key=f"mtarget_picker_{mid}",
                               on_change=_sync_target_from_picker, label_visibility="collapsed")
            with mt2:
                st.text_input("参考目标色 HEX（可选）", value="", key=f"mtarget_text_{mid}",
                             on_change=_sync_target_from_text, label_visibility="collapsed",
                             placeholder="留空 = 纯手动预览，不做色差对比")

            mitem["raw_text"] = st.text_area(
                "色号,比例（一行一个）", value=mitem["raw_text"], key=f"mtext_{mid}",
                height=110,
                help="例如：\nFK-048,76\nFK-021,12\nFK-061,6\nFK-093,6\n"
                    "色号必须已经在色库里；比例不需要凑够 100，会自动归一化。",
            )
            m_grain = st.slider("颗粒视觉尺度", 2, 40, DEFAULT_GRAIN_PX, step=1,
                                key=f"mgrain_{mid}")

            if st.button("生成 / 更新预览", key=f"mgen_{mid}", type="primary"):
                raw_pairs, parse_errors = [], []
                for i, line in enumerate(mitem["raw_text"].strip().split("\n")):
                    line = line.strip()
                    if not line:
                        continue
                    parts = re.split(r"[,，\t]+", line)
                    if len(parts) < 2:
                        parse_errors.append(f"第{i+1}行格式不对：{line}")
                        continue
                    raw_pairs.append((parts[0], parts[1]))
                comps, comp_errors = normalize_manual_components(raw_pairs, lib_df_m)
                all_errors = parse_errors + comp_errors
                for e in all_errors:
                    st.warning(e)
                if not comps:
                    mitem["result"] = None
                    st.error("没有可用的配比行，请检查色号是否在色库里、比例是否是数字。")
                else:
                    mixed_hex_m = partitive_mix_hex(comps)
                    target_text = str(st.session_state.get(f"mtarget_text_{mid}", "")).strip()
                    target_hex_m, delta_e_m = None, None
                    if target_text:
                        th = norm_hex(target_text)
                        if HEX_RE.match(th):
                            target_hex_m = th
                            delta_e_m = manual_delta_e(target_hex_m, mixed_hex_m)
                        else:
                            st.warning("参考目标色 HEX 格式不对，本次先按纯手动预览处理。")
                    mitem["seed"] = random.randint(0, 2**31 - 1)
                    mitem["result"] = {
                        "components": comps, "mixed_hex": mixed_hex_m,
                        "material": mitem["material"], "grain_px": m_grain,
                        "target_hex": target_hex_m, "delta_e": delta_e_m,
                    }

            result = mitem.get("result")
            if result:
                display_target = result.get("target_hex") or result["mixed_hex"]
                png_bytes_m, svg_str_m = render_material_card(
                    {"components": result["components"], "mixed_hex": result["mixed_hex"],
                     "delta_e": result.get("delta_e")},
                    display_target, result["material"], mitem["seed"],
                    result["grain_px"], None, [], mid,
                )
                col_img_m, col_pal_m = st.columns([1.3, 1])
                with col_img_m:
                    show_image(png_bytes_m)
                with col_pal_m:
                    if result.get("target_hex"):
                        st.markdown(render_compare_html(result["target_hex"], result["mixed_hex"]),
                                   unsafe_allow_html=True)
                        st.markdown(delta_e_quality_badge(result["delta_e"]), unsafe_allow_html=True)
                    else:
                        st.markdown(
                            f'<div class="fkcs-compare-item">'
                            f'<div class="fkcs-compare-swatch" style="background:{result["mixed_hex"]}"></div>'
                            f'<div class="fkcs-compare-label">感知混合色（点彩式光学混合）</div>'
                            f'<div class="fkcs-compare-hex">{result["mixed_hex"].upper()}</div></div>',
                            unsafe_allow_html=True,
                        )
                        st.caption("没填参考目标色，纯手动预览模式（填了上面的参考目标色，"
                                  "这里会显示 ΔE00 色差评分）。")
                    st.markdown(render_palette_html(result["components"]), unsafe_allow_html=True)

                mc_dl1, mc_dl2, mc_dl3, mc_dl4 = st.columns(4)
                with mc_dl1:
                    st.download_button(
                        "⬇ SVG", data=svg_str_m.encode("utf-8"),
                        file_name=f"{mitem['name']}_{result['mixed_hex'].lstrip('#')}.svg",
                        mime="image/svg+xml", use_container_width=True, key=f"mdl_svg_{mid}",
                    )
                with mc_dl2:
                    st.download_button(
                        "⬇ PNG", data=png_bytes_m,
                        file_name=f"{mitem['name']}_{result['mixed_hex'].lstrip('#')}.png",
                        mime="image/png", use_container_width=True, key=f"mdl_png_{mid}",
                    )
                with mc_dl3:
                    xlsx_m = build_recipe_workbook([{
                        "formula_id": "手动", "name": mitem["name"],
                        "target_hex": result.get("target_hex") or result["mixed_hex"],
                        "material": result["material"],
                        "delta_e": result.get("delta_e") if result.get("delta_e") is not None else "（无参考目标色）",
                        "components": result["components"],
                    }])
                    st.download_button(
                        "⬇ Excel", data=xlsx_m,
                        file_name=f"{mitem['name']}_配方.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True, key=f"mdl_xlsx_{mid}",
                    )
                with mc_dl4:
                    if st.button("⭐ 收藏", use_container_width=True, key=f"mfav_{mid}"):
                        plan_for_db = {"components": result["components"],
                                      "mixed_hex": result["mixed_hex"],
                                      "delta_e": result.get("delta_e") or 0}
                        db.save_record(
                            result.get("target_hex") or result["mixed_hex"],
                            result["material"], FIXED_TOTAL_WEIGHT,
                            plan_for_db, note=f"⭐手动配色[{mitem['name']}]",
                        )
                        st.success(f"「{mitem['name']}」已收藏到历史记录")

    if remove_mid is not None:
        st.session_state["manual_items"] = [
            m for m in st.session_state["manual_items"] if m["_id"] != remove_mid
        ]

# ===== Tab 3：色库 =====
with tab_lib:
    sub_edit, sub_import = st.tabs(["在线编辑", "批量导入 Excel/CSV"])

    with sub_edit:
        st.caption("可直接在表格里改 HEX、加行、删行。改完点「保存色库」。"
                  "名称/备注选填，不影响配比计算。")
        df = load_library(DATA_CSV)

        c1, c2 = st.columns([2, 1])
        with c1:
            edited = st.data_editor(
                df, num_rows="dynamic", use_container_width=True,
                key="lib_editor", height=430,
            )
            if st.button("保存色库", type="primary"):
                e = edited.copy()
                e["色号"] = e["色号"].astype(str).str.strip()
                e["HEX"] = e["HEX"].astype(str).str.strip().map(norm_hex)
                e["名称"] = e.get("名称", "").fillna("").astype(str)
                e["备注"] = e.get("备注", "").fillna("").astype(str)
                bad = e[~e["HEX"].str.match(HEX_RE)]
                empty = e[e["色号"] == ""]
                if len(bad) > 0:
                    st.error(f"有 {len(bad)} 行 HEX 格式不对（需 #RRGGBB），请修正后再保存。")
                elif len(empty) > 0:
                    st.error("有色号为空的行，请填写或删除。")
                elif e["色号"].duplicated().any():
                    st.error("有重复色号，请修正。")
                else:
                    e[["色号", "HEX", "名称", "备注"]].to_csv(DATA_CSV, index=False)
                    st.success(f"已保存，共 {len(e)} 个色。切到别的 Tab 再回来即可看到最新。")
                    st.cache_data.clear()

        with c2:
            st.markdown("**取色助手**")
            picked = st.color_picker("挑一个颜色", "#1E3A8C")
            st.code(picked.upper(), language=None)
            st.caption("复制上面的 HEX，粘到左侧表格新行即可。")

        st.markdown("---")
        st.markdown("**当前色库预览**")
        prev = load_library(DATA_CSV)
        cols = st.columns(5)
        for i, row in prev.iterrows():
            with cols[i % 5]:
                nm = f"　{row['名称']}" if row["名称"] else ""
                st.markdown(
                    swatch_html(row["HEX"], 20) + f"　{row['色号']}{nm}",
                    unsafe_allow_html=True,
                )

    with sub_import:
        st.caption("支持上传 .xlsx / .xls / .csv，把厂家已有的色号体系批量导入。"
                  "导入前可自由映射列名，同色号会覆盖更新，其余追加。")
        upload = st.file_uploader("选择表格文件", type=["xlsx", "xls", "csv"])

        if upload is not None:
            try:
                if upload.name.lower().endswith(".csv"):
                    raw_df = pd.read_csv(upload, dtype=str)
                else:
                    raw_df = pd.read_excel(upload, dtype=str)
            except Exception as e:
                st.error(f"读取文件失败：{e}")
                raw_df = None

            if raw_df is not None and len(raw_df) > 0:
                st.write(f"读取到 {len(raw_df)} 行，请映射列：")
                cols_available = ["（无）"] + raw_df.columns.tolist()

                def _guess(colnames, keywords):
                    for c in colnames:
                        for kw in keywords:
                            if kw in str(c):
                                return c
                    return None

                guess_code = _guess(raw_df.columns, ["色号", "编号", "code", "Code"])
                guess_hex = _guess(raw_df.columns, ["HEX", "hex", "色值", "颜色值", "RGB"])
                guess_name = _guess(raw_df.columns, ["名称", "颜色名", "name"])
                guess_note = _guess(raw_df.columns, ["备注", "说明", "note", "remark"])

                mc1, mc2, mc3, mc4 = st.columns(4)
                with mc1:
                    col_code = st.selectbox("色号列", raw_df.columns.tolist(),
                                            index=(raw_df.columns.tolist().index(guess_code)
                                                   if guess_code else 0))
                with mc2:
                    col_hex = st.selectbox("HEX/颜色值列", raw_df.columns.tolist(),
                                           index=(raw_df.columns.tolist().index(guess_hex)
                                                  if guess_hex else 0))
                with mc3:
                    col_name = st.selectbox("名称列（可选）", cols_available,
                                            index=(cols_available.index(guess_name)
                                                   if guess_name else 0))
                with mc4:
                    col_note = st.selectbox("备注列（可选）", cols_available,
                                            index=(cols_available.index(guess_note)
                                                   if guess_note else 0))

                st.dataframe(raw_df.head(5), use_container_width=True)

                if st.button("校验并预览导入结果", type="primary"):
                    col_map = {
                        "色号": col_code, "HEX": col_hex,
                        "名称": None if col_name == "（无）" else col_name,
                        "备注": None if col_note == "（无）" else col_note,
                    }
                    imported, problems = import_library_table(raw_df, col_map)
                    st.session_state["import_preview"] = imported
                    st.session_state["import_problems"] = problems

                if "import_preview" in st.session_state:
                    imported = st.session_state["import_preview"]
                    problems = st.session_state["import_problems"]
                    if problems:
                        st.warning(f"发现 {len(problems)} 行问题（这些行不会被导入）：")
                        for idx, msg in problems[:20]:
                            st.text(f"  第 {idx+1} 行：{msg}")
                        if len(problems) > 20:
                            st.text(f"  ……以及另外 {len(problems)-20} 行")
                    bad_idx = {i for i, _ in problems}
                    valid_rows = imported[~imported.index.isin(bad_idx)]
                    st.success(f"可导入 {len(valid_rows)} / {len(imported)} 行")
                    st.dataframe(valid_rows, use_container_width=True, height=300)

                    if st.button("确认导入（同色号覆盖，其余追加）", type="primary"):
                        cur = load_library(DATA_CSV)
                        merged = pd.concat([cur, valid_rows], ignore_index=True)
                        merged = merged.drop_duplicates(subset="色号", keep="last")
                        merged.to_csv(DATA_CSV, index=False)
                        st.success(f"导入完成，色库现有 {len(merged)} 个色。")
                        st.cache_data.clear()
                        del st.session_state["import_preview"]
                        del st.session_state["import_problems"]

# ===== Tab 4：历史记录 =====
with tab_hist:
    records = db.get_records()
    if not records:
        st.info("暂无记录。")
    else:
        if "hist_selected" not in st.session_state:
            st.session_state["hist_selected"] = set()

        top1, top2 = st.columns([1, 3])
        with top1:
            if st.button("删除所选", type="primary"):
                n = db.delete_records(st.session_state["hist_selected"])
                st.session_state["hist_selected"] = set()
                st.success(f"已删除 {n} 条记录。")
                st.rerun()
        with top2:
            st.caption(f"已选中 {len(st.session_state['hist_selected'])} 条")

        for rec in records:
            recipe_txt = "，".join(
                f"{c['色号']} {c['比例']}%" for c in rec.get("recipe", [])
            )
            row_c0, row_c1 = st.columns([0.05, 0.95])
            with row_c0:
                checked = st.checkbox(
                    "", key=f"chk_{rec['id']}",
                    value=rec["id"] in st.session_state["hist_selected"],
                    label_visibility="collapsed",
                )
                if checked:
                    st.session_state["hist_selected"].add(rec["id"])
                else:
                    st.session_state["hist_selected"].discard(rec["id"])
            with row_c1:
                with st.expander(
                    f"#{rec['id']}　{rec['created_at']}　目标 {rec['target_hex']}　"
                    f"混合 {rec['mixed_hex']}　ΔE={rec['delta_e']}"
                ):
                    st.markdown(
                        swatch_html(rec["target_hex"]) + " 目标　　"
                        + swatch_html(rec["mixed_hex"]) + " 混合",
                        unsafe_allow_html=True,
                    )
                    st.write(f"材料：{rec['material']}")
                    st.write(f"配比：{recipe_txt}")
                    st.write(f"备注：{rec['note']}")

                    st.markdown("**打样后补录：**")
                    ar = st.text_input("实际结果（如：偏黑）",
                                       rec.get("actual_result") or "",
                                       key=f"ar_{rec['id']}")
                    fx = st.text_input("修正建议（如：减少 FK-022，增加 FK-021）",
                                       rec.get("fix_suggestion") or "",
                                       key=f"fx_{rec['id']}")
                    bcol1, bcol2 = st.columns(2)
                    with bcol1:
                        if st.button("保存补录", key=f"save_{rec['id']}"):
                            db.update_result(rec["id"], ar, fx)
                            st.success("已保存，刷新可见。")
                    with bcol2:
                        if st.button("删除此记录", key=f"del_{rec['id']}"):
                            db.delete_record(rec["id"])
                            st.success("已删除。")
                            st.rerun()
