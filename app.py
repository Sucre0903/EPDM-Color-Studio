"""
FK Color Studio —— EPDM Granule Mix Designer
==============================================
一键启动请双击 start.bat；或命令行 streamlit run app.py

一个从「配比计算程序」升级为「数字材料设计工作台」的 EPDM 点彩混色工具：
- 左：参数（基础参数常驻，专业参数折叠在「高级设置」里）
- 中：大幅颗粒质感实时预览画布
- 右：配色方案色卡（颜色优先，数据辅助）+ 导出

核心能力：
- 点彩式光学混合评估 + 严格颜色数量 + 颜色锁定 + 点缀杂色 + 颗粒尺度调节；
- 预览同时提供位图（PNG）和矢量+位图混合（SVG，可在 Illustrator/Figma/
  浏览器正常打开、文字可编辑，接近设计师提交材料样板的格式）；
- 色库在线增删改 / Excel 批量导入；历史记录单条与批量删除。
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
import database as db

st.set_page_config(page_title=f"{BRAND_NAME} · {BRAND_SUB}",
                   page_icon="◤", layout="wide")

DATA_CSV = "data/fk_colors.csv"
MATERIALS = ["EPDM", "PP", "PE", "ABS", "PVC", "PC", "PS", "PA", "TPU", "TPE"]
HEX_RE = re.compile(r"^#?[0-9A-Fa-f]{6}$")


# ---------- 全局样式：中性克制的设计工具视觉语言 ----------

def inject_css():
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

    html, body, [class*="css"] {{
        font-family: {FONT_STACK};
    }}

    /* 隐藏 Streamlit 默认顶部工具栏（汉堡菜单/Deploy按钮）和页脚水印——
       之前自定义品牌头部被这条默认工具栏（fixed 定位，白底，高 z-index）
       部分遮挡，就是因为它悬浮在页面最上方；工具本身已有品牌头部，
       这条默认工具栏没有实际用途，直接隐藏掉，问题从根上解决。*/
    [data-testid="stHeader"] {{ display: none; }}
    [data-testid="stDecoration"] {{ display: none; }}
    #MainMenu {{ visibility: hidden; }}
    footer {{ visibility: hidden; }}

    .block-container {{
        padding-top: 2.2rem;
        max-width: 1500px;
    }}

    /* 品牌头部 */
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

    /* 分区小标题（属性面板风格，替代 st.subheader 的厚重感）*/
    .fkcs-label {{
        font-size: 12px; font-weight: 600; color: {INK_FAINT};
        letter-spacing: 1px; text-transform: uppercase;
        margin: 4px 0 8px 0;
    }}

    /* 三栏分隔线，营造工具面板感 */
    [data-testid="column"] {{ padding: 0 16px; }}

    /* 色卡列表（颜色优先，数据辅助）*/
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
        font-size: 13px; font-weight: 600; color: {INK}; width: 40px; text-align: right;
    }}
    .fkcs-palette-kg {{
        font-size: 11.5px; color: {INK_FAINT}; width: 56px; text-align: right;
    }}
    .fkcs-tag {{
        display: inline-block; font-size: 10px; font-weight: 600;
        color: {INK_SOFT}; background: {PAPER}; border: 1px solid {CARD_BORDER};
        border-radius: 4px; padding: 1px 5px; margin-left: 6px;
    }}

    /* 目标色 / 混合色 对照条 */
    .fkcs-compare {{ display: flex; gap: 14px; margin-bottom: 6px; }}
    .fkcs-compare-item {{ flex: 1; }}
    .fkcs-compare-swatch {{
        width: 100%; height: 56px; border-radius: 10px;
        border: 1px solid rgba(0,0,0,0.08); margin-bottom: 6px;
    }}
    .fkcs-compare-label {{ font-size: 11px; color: {INK_FAINT}; }}
    .fkcs-compare-hex {{ font-size: 13px; font-weight: 600; color: {INK}; font-family: monospace; }}

    /* 空画布占位 */
    .fkcs-empty-canvas {{
        display: flex; align-items: center; justify-content: center;
        height: 540px; border: 1.5px dashed {CARD_BORDER}; border-radius: 14px;
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

def _img(path):
    """兼容新旧 Streamlit 的图片宽度参数。"""
    try:
        st.image(path, use_container_width=True)
    except TypeError:
        st.image(path, use_column_width=True)


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
            "比例": f"{c['比例']}%", "用量(kg)": c["用量kg"],
        })
    return pd.DataFrame(rows)


def render_palette_html(components, accent_code=None, locked_codes=None):
    """色彩优先的调色板列表：色块 + 色号 + 比例条 + 数值，替代纯文字表格。"""
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
          <div class="fkcs-palette-kg">{c['用量kg']}kg</div>
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


# ---------- 页面 ----------

tab_gen, tab_lib, tab_hist = st.tabs(["工作台", "色库", "历史记录"])

# ===== Tab 1：工作台（三栏：参数 / 预览画布 / 配色方案）=====
with tab_gen:
    lib_df = load_library(DATA_CSV)
    code_label = {}
    for _, row in lib_df.iterrows():
        label = row["色号"] + (f"（{row['名称']}）" if row["名称"] else "")
        code_label[label] = row["色号"]

    col_left, col_center, col_right = st.columns([0.95, 2.05, 1.15])

    # ---------- 左栏：参数 ----------
    with col_left:
        st.markdown('<div class="fkcs-label">基础参数</div>', unsafe_allow_html=True)
        target = st.color_picker("目标色", "#07166B")
        target_hex = st.text_input("目标色 HEX", target).strip()
        total_weight = st.number_input("总重量 (kg)", min_value=0.1,
                                       value=100.0, step=10.0)
        material = st.selectbox("材料", MATERIALS, index=0)
        n_colors = st.slider("颜色数量", 2, 6, 4,
                             help="严格使用这么多种颗粒，不会被算法自动精简。")

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

        with st.expander("高级设置", expanded=False):
            st.markdown("**锁定必用颜色**")
            locked_labels = st.multiselect(
                "指定必须出现在配比里的颜色（品牌色/库存色），最多 8 个",
                options=list(code_label.keys()), label_visibility="collapsed",
                help="锁定的颜色一定会出现在最终方案里；若数量超过颜色数量"
                    "上限，会自动扩容颜色数量以容纳。",
            )
            locked_codes_input = [code_label[l] for l in locked_labels]

            st.markdown("**点缀杂色**")
            use_accent = st.checkbox(
                "加入点缀杂色（更接近真实 EPDM 铺装的跳色质感）", value=True,
                help="目标偏暗时自动配浅色点缀，目标偏亮时配深色点缀。"
                    "仅在颜色数量 ≥3 时生效。",
            )
            accent_pct = st.slider("点缀杂色比例 (%)", 2.0, 15.0,
                                   DEFAULT_ACCENT_PCT, step=1.0,
                                   disabled=not use_accent)
            st.caption("点缀比例越高，整体观感偏离目标色越明显——这是真实的"
                      "光学混合效应，不是拟合误差，可按需要调节取舍。")

        gen = st.button("生成配比", type="primary", use_container_width=True)

        if gen:
            if not HEX_RE.match(target_hex):
                st.error("目标色 HEX 格式不对，应为 #RRGGBB，例如 #07166B")
            else:
                try:
                    res = search_recipe(norm_hex(target_hex), total_weight,
                                        material, n_colors=n_colors,
                                        use_accent=use_accent,
                                        accent_pct=accent_pct,
                                        locked_codes=locked_codes_input,
                                        csv_path=DATA_CSV)
                    st.session_state["gen_res"] = res
                    st.session_state["gen_material"] = material
                    st.session_state["gen_plan_choice"] = "推荐"
                    # 固定这次生成的随机种子和颗粒尺度快照：后续任何其它交互
                    # 触发的页面重跑都复用这两个值，预览不会无缘无故变化——
                    # 只有再次点击「生成配比」才会换新种子、应用新的颗粒尺度。
                    st.session_state["gen_seed"] = random.randint(0, 2**31 - 1)
                    st.session_state["applied_grain_px"] = grain_px
                except Exception as e:
                    st.error(f"计算失败：{e}")
                    st.session_state.pop("gen_res", None)

    res = st.session_state.get("gen_res")

    # ---------- 中栏：预览画布 ----------
    with col_center:
        st.markdown('<div class="fkcs-label">材料预览</div>', unsafe_allow_html=True)
        if res:
            for note in res.get("notes", []):
                st.warning(note)

            plan_options = {"推荐": res["recommended"]}
            if res.get("darker"):
                plan_options["偏深方案"] = res["darker"]
            if res.get("brighter"):
                plan_options["偏亮方案"] = res["brighter"]

            choice = st.radio("方案", list(plan_options.keys()), horizontal=True,
                             key="gen_plan_choice", label_visibility="collapsed")
            plan = plan_options[choice]

            applied_grain_px = st.session_state.get("applied_grain_px", DEFAULT_GRAIN_PX)
            preview_seed = st.session_state.get("gen_seed")
            tmp_png = os.path.join(tempfile.gettempdir(), "fk_granule.png")
            draw_sample(plan["mixed_hex"], res["target_hex"], plan["components"],
                       material=res["material"], delta_e=plan["delta_e"],
                       accent_code=res["accent_code"],
                       locked_codes=res["locked_codes"],
                       avg_grain_px=applied_grain_px, seed=preview_seed,
                       out_path=tmp_png)
            _img(tmp_png)

            svg_str = build_svg(plan["mixed_hex"], res["target_hex"],
                               plan["components"], material=res["material"],
                               delta_e=plan["delta_e"],
                               accent_code=res["accent_code"],
                               locked_codes=res["locked_codes"],
                               avg_grain_px=applied_grain_px, seed=preview_seed)

            c_dl1, c_dl2, c_dl3 = st.columns(3)
            with c_dl1:
                st.download_button(
                    "⬇ SVG（可编辑矢量）",
                    data=svg_str.encode("utf-8"),
                    file_name=f"epdm_{plan['mixed_hex'].lstrip('#')}.svg",
                    mime="image/svg+xml", use_container_width=True,
                )
            with c_dl2:
                with open(tmp_png, "rb") as f:
                    st.download_button(
                        "⬇ PNG（位图）",
                        data=f.read(),
                        file_name=f"epdm_{plan['mixed_hex'].lstrip('#')}.png",
                        mime="image/png", use_container_width=True,
                    )
            with c_dl3:
                if st.button("⭐ 收藏方案", use_container_width=True):
                    rid = db.save_record(
                        res["target_hex"], res["material"], res["total_weight"],
                        plan, note=f"⭐{res['n_colors']}色[{choice}]"
                        + (f"+点缀{res['accent_code']}" if res["used_accent"] else "")
                        + (f"+锁定{','.join(res['locked_codes'])}" if res["locked_codes"] else ""),
                    )
                    st.success(f"已收藏到历史记录（id={rid}）")
        else:
            st.markdown(
                '<div class="fkcs-empty-canvas">设置左侧参数，点击「生成配比」<br>'
                '在这里查看颗粒材质实时预览</div>',
                unsafe_allow_html=True,
            )

    # ---------- 右栏：配色方案 ----------
    with col_right:
        st.markdown('<div class="fkcs-label">配色方案</div>', unsafe_allow_html=True)
        if res:
            plan_options = {"推荐": res["recommended"]}
            if res.get("darker"):
                plan_options["偏深方案"] = res["darker"]
            if res.get("brighter"):
                plan_options["偏亮方案"] = res["brighter"]
            choice = st.session_state.get("gen_plan_choice", "推荐")
            plan = plan_options[choice]

            st.markdown(render_compare_html(res["target_hex"], plan["mixed_hex"]),
                       unsafe_allow_html=True)
            st.caption(f"ΔE00（点彩感知）= {plan['delta_e']}　·　{res['n_colors']} 色"
                      + ("　·　含点缀" if res["used_accent"] else ""))

            st.markdown(
                render_palette_html(plan["components"], res["accent_code"],
                                    res["locked_codes"]),
                unsafe_allow_html=True,
            )

            with st.expander("查看数据表格 / 其它方案对比"):
                st.dataframe(
                    plan_to_df(plan, res["accent_code"], res["locked_codes"]),
                    hide_index=True, use_container_width=True,
                )
                for name, p in plan_options.items():
                    if name == choice:
                        continue
                    st.markdown(f"**{name}**　ΔE00 = {p['delta_e']}")
                    st.markdown(
                        render_palette_html(p["components"], res["accent_code"],
                                            res["locked_codes"]),
                        unsafe_allow_html=True,
                    )
        else:
            st.caption("生成配比后，这里会显示配色方案、色卡和导出选项。")

# ===== Tab 2：色库 =====
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
                        del st.session_state["import_preview"]
                        del st.session_state["import_problems"]

# ===== Tab 3：历史记录 =====
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
                    st.write(f"材料：{rec['material']}　总重：{rec['total_weight']}kg")
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
