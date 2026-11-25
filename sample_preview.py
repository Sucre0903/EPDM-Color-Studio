"""
EPDM 颗粒铺装预览图
====================
不再画单一综合色块，而是按最终配比把独立颗粒真实随机铺开——
每颗颗粒保持自己的原色，靠数量和面积占比在整体上形成视觉观感，
这是「点彩式」而非「颜料式」的正确呈现方式。

本模块提供两种输出：
- draw_sample()  ：快速位图预览（PNG），供程序内即时查看；
- build_svg()    ：颗粒质感位图 + 文字/图例矢量元素混合的 SVG，
                   可在 Illustrator / Inkscape 里编辑文字、做高清导出。

颗粒生成思路：
1. 在画布上撒一批随机抖动的网格种子点（近似泊松分布，颗粒大小均匀自然）；
2. 用 cKDTree 对每个像素找最近种子 —— 等价于算一张 Voronoi 图，
   但用最近邻栅格化实现，快且稳，天然产生棱角分明的颗粒状分区；
3. 颜色分配采用「蛇形空间排序 + 比例配额均衡交织」算法（而非纯随机打乱），
   避免同色颗粒大量连续聚集，同时保留局部随机感（见 _balanced_sequence）；
4. 每颗颗粒加随机明暗抖动模拟天然颗粒质感；颗粒边界描一圈暗缝，
   模拟真实颗粒间的缝隙阴影；
5. 超采样渲染后降采样，让边缘更接近真实照片的柔和感。
"""

import base64
import io
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.spatial import cKDTree

from color_utils import hex_to_rgb

DEFAULT_GRAIN_PX = 15   # 默认颗粒尺度（越小=颗粒越细/近似远观，越大=颗粒越粗/近观质感）


def _load_font(size):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _text_color_for(rgb):
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return (30, 30, 30) if lum > 150 else (245, 245, 245)


def _balanced_sequence(counts, rng, window=10):
    """
    生成一个长度为 sum(counts) 的颜色序列，满足：
    - 每种颜色出现次数精确等于 counts[i]；
    - 用「配额亏欠」贪心法尽量均匀交织，避免任何颜色连续扎堆
      （同色最大连续长度理论上不超过约 1/该色占比）；
    - 再做小窗口内局部随机重排，保留自然随机感而不破坏整体均匀性。
    这是解决「深色颗粒连续聚集成色斑」问题的核心。
    """
    n_colors = len(counts)
    total = int(sum(counts))
    if total == 0:
        return np.array([], dtype=int)
    target_frac = np.asarray(counts, dtype=float) / total
    seq = np.empty(total, dtype=int)
    so_far = np.zeros(n_colors, dtype=float)
    for i in range(total):
        deficit = target_frac * (i + 1) - so_far
        c = int(np.argmax(deficit))
        seq[i] = c
        so_far[c] += 1
    if window and window > 1:
        for start in range(0, total, window):
            end = min(start + window, total)
            idx = np.arange(start, end)
            perm = rng.permutation(idx)
            seq[idx] = seq[perm]
    return seq


def generate_granule_field(components, size=720, avg_grain_px=DEFAULT_GRAIN_PX,
                           seed=None, edge_darken=0.55, jitter=0.16,
                           anti_cluster_window=10):
    """
    生成颗粒铺装的像素数组（size x size x 3，uint8）。
    components: [{"色号","HEX","比例"}, ...]，比例之和应为 100。
    avg_grain_px: 颗粒视觉尺度（近似平均颗粒直径，像素）。数值越小颗粒越细密
                  （模拟远观效果），数值越大颗粒越粗（模拟近距离观察）。
    anti_cluster_window: 均衡交织算法的局部打乱窗口，越小颗粒分布越均匀规整，
                  越大越接近纯随机（可能出现同色小聚集）。
    返回 (pixel_array, realized_pct_dict)。
    """
    rng = np.random.default_rng(seed)
    avg_grain_px = max(4, float(avg_grain_px))

    # ---- 1. 撒种子点（抖动网格，近似泊松分布，颗粒大小均匀自然）----
    spacing = avg_grain_px
    n_cells = max(2, int(size / spacing))
    xs = (np.arange(n_cells) + 0.5) * spacing
    ys = (np.arange(n_cells) + 0.5) * spacing
    gx, gy = np.meshgrid(xs, ys)  # shape (n_cells, n_cells)：行=y，列=x
    gx = gx + rng.uniform(-0.5, 0.5, gx.shape) * spacing
    gy = gy + rng.uniform(-0.5, 0.5, gy.shape) * spacing

    # 蛇形（boustrophedon）空间序：同一行内奇偶行反向，保证序列相邻的种子
    # 在空间上也大概率相邻（普通逐行从左到右在换行处会有大跳跃，蛇形能避免）。
    row_order = np.arange(n_cells)
    serp_rows = []
    for r in row_order:
        cols = np.arange(n_cells)
        if r % 2 == 1:
            cols = cols[::-1]
        serp_rows.append(np.stack([np.full(n_cells, r), cols], axis=1))
    serp_idx = np.concatenate(serp_rows, axis=0)  # (n_seeds, 2) -> (row, col)

    seeds = np.stack([gx[serp_idx[:, 0], serp_idx[:, 1]],
                      gy[serp_idx[:, 0], serp_idx[:, 1]]], axis=1)
    n_seeds = seeds.shape[0]

    # ---- 2. 按比例分层计数 + 均衡交织分配颜色（避免同色扎堆，同时保证面积精确）----
    codes = [c["色号"] for c in components]
    ratios = np.array([c["比例"] for c in components], dtype=float)
    ratios = ratios / ratios.sum()
    counts = np.floor(ratios * n_seeds).astype(int)
    counts[np.argmax(ratios)] += n_seeds - counts.sum()  # 补齐取整误差

    color_seq = _balanced_sequence(counts, rng, window=anti_cluster_window)
    color_idx = color_seq  # 已经是按「蛇形空间序」排列的颜色分配

    realized_pct = {
        codes[i]: round(float((color_idx == i).sum()) / n_seeds * 100, 1)
        for i in range(len(components))
    }

    base_rgbs = np.array([hex_to_rgb(c["HEX"]) for c in components])
    seed_rgb = base_rgbs[color_idx].astype(float)
    # 每颗颗粒随机明暗抖动，模拟天然颗粒的光照差异质感
    shade = rng.uniform(1 - jitter, 1 + jitter, size=(n_seeds, 1))
    seed_rgb = np.clip(seed_rgb * shade, 0, 255)

    # ---- 3. 最近邻栅格化 = Voronoi 图（用 KDTree 加速）----
    tree = cKDTree(seeds)
    xx, yy = np.meshgrid(np.arange(size), np.arange(size))
    pts = np.stack([xx.ravel(), yy.ravel()], axis=1).astype(float)
    _, idx = tree.query(pts, k=1)
    id_grid = idx.reshape(size, size)

    pixel_rgb = seed_rgb[id_grid]

    # ---- 4. 颗粒边界描暗缝（模拟真实颗粒间的缝隙阴影）----
    edge = np.zeros((size, size), dtype=bool)
    edge[:-1, :] |= id_grid[1:, :] != id_grid[:-1, :]
    edge[1:, :] |= id_grid[1:, :] != id_grid[:-1, :]
    edge[:, :-1] |= id_grid[:, 1:] != id_grid[:, :-1]
    edge[:, 1:] |= id_grid[:, 1:] != id_grid[:, :-1]

    out = pixel_rgb.copy()
    out[edge] = out[edge] * edge_darken
    out = np.clip(out, 0, 255).astype(np.uint8)

    return out, realized_pct


def _render_granule_png_bytes(components, px_size, avg_grain_px, seed):
    """渲染颗粒纹理为 PNG 字节流（供位图预览和 SVG 内嵌复用）。"""
    SS = px_size * 2  # 超采样后降采样，边缘更柔和
    field, realized_pct = generate_granule_field(
        components, size=SS, avg_grain_px=avg_grain_px * 2, seed=seed,
    )
    img = Image.fromarray(field, "RGB").resize((px_size, px_size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue(), realized_pct, img


# ---------- 位图预览（PNG，程序内快速查看）----------

def draw_sample(mixed_hex, target_hex, components,
                material="EPDM", delta_e=None, out_path="preview.png",
                accent_code=None, locked_codes=None, seed=None,
                avg_grain_px=DEFAULT_GRAIN_PX):
    """
    生成 EPDM 颗粒铺装预览图（位图 PNG）并保存，返回路径。
    图例区域按颜色数量动态铺开，不再受固定行数限制；带品牌头部
    （真实 Logo 素材 + 工作台名称），与 SVG 导出版本视觉统一。
    """
    from branding import (INK, INK_SOFT, INK_FAINT, PAPER, CARD_BORDER,
                          BRAND_NAME, BRAND_SUB, logo_mark_pil)

    def _hex_to_rgb_tuple(h):
        return tuple(int(c) for c in hex_to_rgb(h))

    locked_codes = set(locked_codes or [])
    S = 460
    px = 60
    header_h = 108
    py = header_h + 24
    radius = 22

    png_bytes, realized_pct, plate_img = _render_granule_png_bytes(
        components, S, avg_grain_px, seed,
    )

    n_rows = len(components)
    row_h = 38
    legend_top = py + S + 34
    legend_h = 34 + n_rows * row_h
    footer_h = 34
    H = legend_top + legend_h + footer_h
    W = 980

    bg = _hex_to_rgb_tuple(PAPER)
    img = Image.new("RGB", (W, H), bg)

    base_rgb = _hex_to_rgb_tuple(mixed_hex)

    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [px + 10, py + 16, px + S + 10, py + S + 16],
        radius=radius, fill=(0, 0, 0, 70),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(12))
    img = Image.alpha_composite(img.convert("RGBA"), shadow).convert("RGB")
    draw = ImageDraw.Draw(img)

    border_rgb = _hex_to_rgb_tuple(CARD_BORDER)
    ink_rgb = _hex_to_rgb_tuple(INK)
    ink_soft_rgb = _hex_to_rgb_tuple(INK_SOFT)
    ink_faint_rgb = _hex_to_rgb_tuple(INK_FAINT)

    # ---- 品牌头部：真实 Logo 素材（原图比例，非重绘）----
    logo_img = logo_mark_pil(30)
    img.paste(logo_img, (60, 24), logo_img)
    f_brand = _load_font(19)
    f_brand_sub = _load_font(11)
    draw.text((100, 27), BRAND_NAME, font=f_brand, fill=ink_rgb)
    draw.text((100, 48), BRAND_SUB.upper(), font=f_brand_sub, fill=ink_faint_rgb)
    draw.line([(60, header_h - 14), (W - 60, header_h - 14)], fill=border_rgb, width=1)

    f_label = _load_font(23)
    f_small = _load_font(19)
    draw.text((60, header_h + 2), f"材料：{material}　颗粒尺度：{avg_grain_px:.0f}px",
              font=f_small, fill=ink_faint_rgb)

    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1],
                                           radius=radius, fill=255)
    img.paste(plate_img, (px, py), mask)
    draw.rounded_rectangle([px, py, px + S, py + S], radius=radius,
                           outline=border_rgb, width=1)

    rx = px + S + 44
    ry = py + 2
    draw.text((rx, ry), "目标色", font=f_label, fill=ink_soft_rgb)
    draw.rounded_rectangle([rx, ry + 30, rx + 74, ry + 104], radius=10,
                           fill=_hex_to_rgb_tuple(target_hex), outline=border_rgb)
    draw.text((rx + 86, ry + 56), target_hex.upper(), font=f_small, fill=ink_soft_rgb)

    draw.text((rx, ry + 128), "感知混合色", font=f_label, fill=ink_soft_rgb)
    draw.rounded_rectangle([rx, ry + 158, rx + 74, ry + 232], radius=10,
                           fill=base_rgb, outline=border_rgb)
    draw.text((rx + 86, ry + 184), mixed_hex.upper(), font=f_small, fill=ink_soft_rgb)
    if delta_e is not None:
        draw.text((rx + 86, ry + 208), f"ΔE00(点彩感知) = {delta_e}",
                  font=f_small, fill=ink_faint_rgb)
    draw.text((rx, ry + 252), "* 远观整体色，非颗粒真实颜色",
              font=f_small, fill=ink_faint_rgb)
    extra_y = ry + 282
    if accent_code:
        draw.text((rx, extra_y), f"含点缀杂色：{accent_code}",
                  font=f_small, fill=(150, 110, 40))
        extra_y += 28
    if locked_codes:
        draw.text((rx, extra_y), f"锁定色：{', '.join(sorted(locked_codes))}",
                  font=f_small, fill=(80, 110, 150))

    by = legend_top
    draw.text((60, by), "配比（目标比例 / 实际颗粒占比）", font=f_label, fill=ink_rgb)
    ly = by + 34
    for c in components:
        chip = _hex_to_rgb_tuple(c["HEX"])
        draw.rounded_rectangle([60, ly, 90, ly + 28], radius=5, fill=chip,
                               outline=(170, 170, 170))
        real = realized_pct.get(c["色号"], c["比例"])
        tags = []
        if c["色号"] == accent_code:
            tags.append("点缀")
        if c["色号"] in locked_codes:
            tags.append("锁定")
        tag = f"（{'/'.join(tags)}）" if tags else ""
        txt = f"{c['色号']}{tag}  目标 {c['比例']}%　实际 {real}%"
        draw.text((102, ly + 3), txt, font=f_small, fill=ink_soft_rgb)
        ly += row_h

    draw.text((60, H - 24),
              "* 程序按比例真实随机铺装生成，颗粒质感为方向确认，非最终实物照片",
              font=f_small, fill=ink_faint_rgb)

    img.save(out_path)
    return out_path


# ---------- SVG 输出（颗粒位图 + 文字/图例矢量，供后续编辑与高清导出）----------

def _xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def build_svg(mixed_hex, target_hex, components,
              material="EPDM", delta_e=None, accent_code=None,
              locked_codes=None, seed=None, avg_grain_px=DEFAULT_GRAIN_PX):
    """
    生成标准 SVG 字符串：颗粒质感部分是内嵌位图（保证随机纹理的真实感），
    标题 / 色号 / 比例 / 图例 / 品牌 Logo 等全部是矢量 <text>/<rect>/<polygon>
    元素 —— 可在 Illustrator / Photoshop / Figma / 浏览器里正常打开，
    文字可编辑、缩放不失真，效果接近设计师提交的材料样板而非截图。
    返回 SVG 字符串。
    """
    from branding import (INK, INK_SOFT, INK_FAINT, PAPER, CARD_BORDER,
                          FONT_STACK, BRAND_NAME, BRAND_SUB,
                          logo_mark_svg_image)

    locked_codes = set(locked_codes or [])
    S = 460
    px = 60
    header_h = 108   # 品牌头部区域高度
    py = header_h + 24
    radius = 22

    png_bytes, realized_pct, _ = _render_granule_png_bytes(
        components, S * 2, avg_grain_px, seed,  # 2x 分辨率，供矢量文件高清导出
    )
    b64 = base64.b64encode(png_bytes).decode("ascii")

    n_rows = len(components)
    row_h = 38
    legend_top = py + S + 34
    legend_h = 34 + n_rows * row_h
    footer_h = 34
    H = legend_top + legend_h + footer_h
    W = 980

    base_rgb = tuple(int(c) for c in hex_to_rgb(mixed_hex))
    target_rgb = tuple(int(c) for c in hex_to_rgb(target_hex))

    def T(x, y, size, fill, text, weight="400", anchor="start"):
        """统一的矢量文字生成，每个 <text> 都自带 font-family，兼容各类阅读器。"""
        return (f'<text x="{x}" y="{y}" font-family="{FONT_STACK}" '
               f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
               f'text-anchor="{anchor}">{_xml_escape(text)}</text>')

    parts = []
    # SVG 1.1 根节点必须声明 xlink 命名空间，否则 xlink:href 在 Illustrator /
    # Figma / 严格 XML 解析器里会报 "unbound prefix" 而无法打开 —— 这是修复的核心。
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
    )
    parts.append(f'<rect x="0" y="0" width="{W}" height="{H}" fill="{PAPER}"/>')

    # ---- 品牌头部：Logo 标志 + 工作台名称 ----
    parts.append(logo_mark_svg_image(size=34, x=60, y=34))
    parts.append(T(104, 58, 21, INK, BRAND_NAME, weight="600"))
    parts.append(T(104, 78, 12.5, INK_FAINT, BRAND_SUB.upper()))
    parts.append(f'<line x1="60" y1="{header_h-14}" x2="{W-60}" y2="{header_h-14}" '
                f'stroke="{CARD_BORDER}" stroke-width="1"/>')
    parts.append(T(60, header_h + 8, 13, INK_FAINT,
                  f"材料 {material}　颗粒尺度 {avg_grain_px:.0f}px　"
                  f"生成时间戳 seed={seed if seed is not None else '-'}"))

    # 颗粒位图（圆角裁剪）—— clipPath 是非渲染定义，规范做法放进 <defs>，
    # 兼容性更好；id 带上颜色特征，避免多个导出文件被放进同一页面时 id 冲突。
    clip_id = f"granuleClip_{mixed_hex.lstrip('#')}"
    parts.append(
        f'<defs><clipPath id="{clip_id}"><rect x="{px}" y="{py}" width="{S}" '
        f'height="{S}" rx="{radius}" ry="{radius}"/></clipPath></defs>'
    )
    parts.append(
        f'<rect x="{px+8}" y="{py+12}" width="{S}" height="{S}" rx="{radius}" '
        f'ry="{radius}" fill="#000000" opacity="0.10"/>'
    )
    # href 用 SVG2 标准写法 + xlink:href 兼容旧版解析器，双保险
    parts.append(
        f'<image x="{px}" y="{py}" width="{S}" height="{S}" '
        f'clip-path="url(#{clip_id})" '
        f'href="data:image/png;base64,{b64}" '
        f'xlink:href="data:image/png;base64,{b64}" '
        f'preserveAspectRatio="xMidYMid slice"/>'
    )
    parts.append(
        f'<rect x="{px}" y="{py}" width="{S}" height="{S}" rx="{radius}" '
        f'ry="{radius}" fill="none" stroke="{CARD_BORDER}" stroke-width="1"/>'
    )

    # 右侧信息
    rx = px + S + 44
    ry = py + 2
    parts.append(T(rx, ry + 18, 18, INK_SOFT, "目标色"))
    parts.append(f'<rect x="{rx}" y="{ry+34}" width="74" height="74" rx="10" '
                 f'fill="rgb{target_rgb}" stroke="{CARD_BORDER}"/>')
    parts.append(T(rx + 86, ry + 60, 17, INK_SOFT, target_hex.upper()))

    parts.append(T(rx, ry + 142, 18, INK_SOFT, "感知混合色"))
    parts.append(f'<rect x="{rx}" y="{ry+164}" width="74" height="74" rx="10" '
                 f'fill="rgb{base_rgb}" stroke="{CARD_BORDER}"/>')
    parts.append(T(rx + 86, ry + 190, 17, INK_SOFT, mixed_hex.upper()))
    if delta_e is not None:
        parts.append(T(rx + 86, ry + 214, 17, INK_FAINT,
                      f"\u0394E00(点彩感知) = {delta_e}"))
    parts.append(T(rx, ry + 258, 15, INK_FAINT, "* 远观整体色，非颗粒真实颜色"))
    extra_y = ry + 288
    if accent_code:
        parts.append(T(rx, extra_y, 17, "#966E28", f"含点缀杂色：{accent_code}"))
        extra_y += 28
    if locked_codes:
        parts.append(T(rx, extra_y, 17, "#506E96",
                      f"锁定色：{', '.join(sorted(locked_codes))}"))

    # 图例（矢量，逐色号动态铺开，不受行数限制）
    by = legend_top
    parts.append(T(60, by + 16, 18, INK, "配比（目标比例 / 实际颗粒占比）", weight="600"))
    ly = by + 34
    for c in components:
        chip_hex = c["HEX"]
        real = realized_pct.get(c["色号"], c["比例"])
        tags = []
        if c["色号"] == accent_code:
            tags.append("点缀")
        if c["色号"] in locked_codes:
            tags.append("锁定")
        tag = f"（{'/'.join(tags)}）" if tags else ""
        txt = f"{c['色号']}{tag}  目标 {c['比例']}%　实际 {real}%"
        parts.append(f'<rect x="60" y="{ly}" width="30" height="28" rx="5" '
                     f'fill="{chip_hex}" stroke="#AAAAAA"/>')
        parts.append(T(102, ly + 20, 17, INK_SOFT, txt))
        ly += row_h

    parts.append(T(60, H - 16, 12.5, INK_FAINT,
                  "* 程序按比例真实随机铺装生成，颗粒质感为方向确认，非最终实物照片"))

    parts.append('</svg>')
    svg_str = "".join(parts)

    # 生成后立即用严格 XML 解析器自检，任何格式问题在这里就会暴露，
    # 不会把打不开的文件交付给使用者。
    import xml.dom.minidom as _minidom
    _minidom.parseString(svg_str)

    return svg_str


if __name__ == "__main__":
    import time
    from ratio_search import search_recipe

    res = search_recipe("#07166B", 100, "EPDM", n_colors=4, use_accent=True)
    plan = res["recommended"]

    t = time.time()
    p = draw_sample(plan["mixed_hex"], res["target_hex"], plan["components"],
                    material=res["material"], delta_e=plan["delta_e"],
                    accent_code=res["accent_code"], out_path="granule_demo.png")
    print(f"PNG 已生成: {p}  (耗时 {time.time()-t:.2f}s)")

    t = time.time()
    svg = build_svg(plan["mixed_hex"], res["target_hex"], plan["components"],
                    material=res["material"], delta_e=plan["delta_e"],
                    accent_code=res["accent_code"])
    with open("granule_demo.svg", "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"SVG 已生成: granule_demo.svg  (耗时 {time.time()-t:.2f}s, "
         f"大小 {len(svg)/1024:.1f}KB)")

    # 6色测试：验证图例不再受4行限制
    res6 = search_recipe("#EC7539", 100, "EPDM", n_colors=6, use_accent=True)
    plan6 = res6["recommended"]
    draw_sample(plan6["mixed_hex"], res6["target_hex"], plan6["components"],
               material="EPDM", delta_e=plan6["delta_e"],
               accent_code=res6["accent_code"], out_path="granule_6color.png")
    print(f"6色图例测试：共{len(plan6['components'])}行，已生成 granule_6color.png")
