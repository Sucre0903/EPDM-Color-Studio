"""
品牌视觉规范
============
统一管理 Logo 图形、配色系统、字体栈 —— App 界面和 SVG 导出文件共用
同一套定义，保证视觉一致（设计工具本身也应该是"设计好的"）。

Logo 说明：直接使用张唐景观（Z+T Studio）提供的原始 Logo 图形文件
（assets/logo.png，已精确裁剪到图形边界框、白底转透明），保证比例、
细节、颜色与原始素材完全一致，不做几何重绘、不引入解读误差。
"""

import base64
import os

# ---------- 中性色系统（弱化"管理后台红"，让 EPDM 颜色本身成为视觉主角）----------
INK = "#232326"          # 主文字 / 深色元素
INK_SOFT = "#5B5B60"      # 次要文字
INK_FAINT = "#9A9A9E"     # 弱化说明文字 / 分隔线
PAPER = "#FAFAF9"         # 页面底色（非纯白，更接近材质卡纸感）
CARD = "#FFFFFF"          # 卡片底色
CARD_BORDER = "#E7E5E2"   # 卡片描边
ACCENT = "#4A4A4E"        # 强调色（按钮等），克制的石墨灰，替代 Streamlit 默认红

FONT_STACK = ("'Inter','Helvetica Neue','PingFang SC','Noto Sans CJK SC',"
             "'Microsoft YaHei',Arial,sans-serif")

BRAND_NAME = "ZT Color Match Studio"
BRAND_SUB = "EPDM Granule Mix Designer"

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_LOGO_PATH = os.path.join(_ASSETS_DIR, "logo.png")

_logo_b64_cache = None


def _logo_base64():
    """读取 assets/logo.png 并缓存其 base64 编码，供 HTML/SVG 内嵌复用。"""
    global _logo_b64_cache
    if _logo_b64_cache is None:
        with open(_LOGO_PATH, "rb") as f:
            _logo_b64_cache = base64.b64encode(f.read()).decode("ascii")
    return _logo_b64_cache


def logo_mark_html(size=32):
    """返回可直接用于 st.markdown(unsafe_allow_html=True) 的 <img> 标签。"""
    b64 = _logo_base64()
    return (f'<img src="data:image/png;base64,{b64}" width="{size}" '
           f'height="{size}" style="display:inline-block;vertical-align:middle;" />')


def logo_mark_svg_image(size=34, x=0, y=0):
    """
    返回可嵌入 SVG 文件的 <image> 片段（原始 Logo 位图，保证与品牌素材
    完全一致；SVG 导出文件里颗粒纹理本身也是位图内嵌，做法一致）。
    """
    b64 = _logo_base64()
    return (f'<image x="{x}" y="{y}" width="{size}" height="{size}" '
           f'href="data:image/png;base64,{b64}" '
           f'xlink:href="data:image/png;base64,{b64}" '
           f'preserveAspectRatio="xMidYMid meet"/>')


def logo_mark_pil(size=34):
    """返回 PIL Image 对象（RGBA），供 draw_sample 直接 paste 到画布上。"""
    from PIL import Image
    img = Image.open(_LOGO_PATH).convert("RGBA")
    return img.resize((size, size), Image.LANCZOS)
