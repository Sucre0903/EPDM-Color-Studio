"""
颜色工具模块
============
提供 HEX/RGB/Lab 之间的转换、颜色加权混合、以及 CIEDE2000 色差计算。

设计原则（第一阶段 MVP）：
- 混合用「重量加权平均」，简单直观，符合色母 " 加黑变暗 " 的直觉。
- 色差用 CIEDE2000，比 RGB 欧氏距离更贴近人眼感受，匹配结果更可信。
- 全部用 numpy 实现，不依赖 colormath 等易踩坑的库。
"""

import numpy as np


# ---------- HEX / RGB ----------

def hex_to_rgb(hex_str):
    """'#07166B' -> np.array([7, 22, 107])，值域 0-255"""
    hex_str = hex_str.strip().lstrip("#")
    if len(hex_str) == 3:  # 支持 #abc 简写
        hex_str = "".join(c * 2 for c in hex_str)
    r = int(hex_str[0:2], 16)
    g = int(hex_str[2:4], 16)
    b = int(hex_str[4:6], 16)
    return np.array([r, g, b], dtype=float)


def rgb_to_hex(rgb):
    """np.array([7, 22, 107]) -> '#07166B'"""
    rgb = np.clip(np.round(rgb), 0, 255).astype(int)
    return "#{:02X}{:02X}{:02X}".format(*rgb)


# ---------- RGB / Lab ----------
# 标准 sRGB (D65) 转换流程：sRGB -> 线性 RGB -> XYZ -> Lab

def _srgb_to_linear(c):
    c = c / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def rgb_to_xyz(rgb):
    r, g, b = _srgb_to_linear(np.asarray(rgb, dtype=float))
    # sRGB D65 矩阵
    x = r * 0.4124 + g * 0.3576 + b * 0.1805
    y = r * 0.2126 + g * 0.7152 + b * 0.0722
    z = r * 0.0193 + g * 0.1192 + b * 0.9505
    return np.array([x, y, z]) * 100.0


def xyz_to_lab(xyz):
    # D65 参考白
    ref = np.array([95.047, 100.0, 108.883])
    xyz = xyz / ref

    def f(t):
        delta = 6.0 / 29.0
        return np.where(t > delta ** 3, np.cbrt(t), t / (3 * delta ** 2) + 4.0 / 29.0)

    fx, fy, fz = f(xyz)
    L = 116 * fy - 16
    a = 500 * (fx - fy)
    b = 200 * (fy - fz)
    return np.array([L, a, b])


def rgb_to_lab(rgb):
    return xyz_to_lab(rgb_to_xyz(rgb))


def hex_to_lab(hex_str):
    return rgb_to_lab(hex_to_rgb(hex_str))


# ---------- 色差 ----------

def delta_e_cie76(lab1, lab2):
    """CIE76：Lab 空间欧氏距离，简单快速，作为兜底。"""
    return float(np.sqrt(np.sum((lab1 - lab2) ** 2)))


def delta_e_ciede2000(lab1, lab2):
    """
    CIEDE2000 色差，行业标准。数值越小两色越接近。
    lab1, lab2: [L, a, b]
    """
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2

    avg_L = (L1 + L2) / 2.0
    C1 = np.sqrt(a1 ** 2 + b1 ** 2)
    C2 = np.sqrt(a2 ** 2 + b2 ** 2)
    avg_C = (C1 + C2) / 2.0

    G = 0.5 * (1 - np.sqrt(avg_C ** 7 / (avg_C ** 7 + 25 ** 7)))
    a1p = (1 + G) * a1
    a2p = (1 + G) * a2
    C1p = np.sqrt(a1p ** 2 + b1 ** 2)
    C2p = np.sqrt(a2p ** 2 + b2 ** 2)
    avg_Cp = (C1p + C2p) / 2.0

    def _hp(bp, ap):
        h = np.degrees(np.arctan2(bp, ap))
        return h + 360 if h < 0 else h

    h1p = _hp(b1, a1p)
    h2p = _hp(b2, a2p)

    if C1p * C2p == 0:
        delta_hp = 0
    elif abs(h1p - h2p) <= 180:
        delta_hp = h2p - h1p
    elif h2p - h1p > 180:
        delta_hp = h2p - h1p - 360
    else:
        delta_hp = h2p - h1p + 360

    delta_Lp = L2 - L1
    delta_Cp = C2p - C1p
    delta_Hp = 2 * np.sqrt(C1p * C2p) * np.sin(np.radians(delta_hp) / 2.0)

    if C1p * C2p == 0:
        avg_hp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        avg_hp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        avg_hp = (h1p + h2p + 360) / 2.0
    else:
        avg_hp = (h1p + h2p - 360) / 2.0

    T = (1 - 0.17 * np.cos(np.radians(avg_hp - 30))
         + 0.24 * np.cos(np.radians(2 * avg_hp))
         + 0.32 * np.cos(np.radians(3 * avg_hp + 6))
         - 0.20 * np.cos(np.radians(4 * avg_hp - 63)))

    delta_ro = 30 * np.exp(-(((avg_hp - 275) / 25) ** 2))
    Rc = 2 * np.sqrt(avg_Cp ** 7 / (avg_Cp ** 7 + 25 ** 7))
    Sl = 1 + (0.015 * (avg_L - 50) ** 2) / np.sqrt(20 + (avg_L - 50) ** 2)
    Sc = 1 + 0.045 * avg_Cp
    Sh = 1 + 0.015 * avg_Cp * T
    Rt = -np.sin(np.radians(2 * delta_ro)) * Rc

    kL = kC = kH = 1.0
    dE = np.sqrt(
        (delta_Lp / (kL * Sl)) ** 2
        + (delta_Cp / (kC * Sc)) ** 2
        + (delta_Hp / (kH * Sh)) ** 2
        + Rt * (delta_Cp / (kC * Sc)) * (delta_Hp / (kH * Sh))
    )
    return float(dE)


def delta_e(hex1, hex2):
    """便捷入口：两个 HEX 之间的 CIEDE2000 色差。"""
    return delta_e_ciede2000(hex_to_lab(hex1), hex_to_lab(hex2))


# ---------- 混合 ----------
#
# 两种物理上完全不同的混合模型：
#
# 1) mix_colors（颜料式 / 减色混合）：直接在 sRGB（伽马）数值上做加权平均。
#    适用于「颜料真正融合成一种新物质」的场景（如油漆、染料）。
#
# 2) partitive_mix（点彩式 / 光学混合，也叫「空间混色」）：颗粒本身颜色不变，
#    只是在空间上离散分布；人眼在一定距离外因为分辨率有限，会把落在同一
#    视锥角内的多个颗粒反射的光在视网膜上做能量积分。光是线性叠加的，
#    所以正确做法是：先把每个颜色从 sRGB 解码到线性光空间，按「面积占比」
#    加权平均，再编码回 sRGB。这是点彩画派、半色调印刷、EPDM 颗粒地坪
#    等「离散颗粒 + 远观混色」场景的正确模型 —— 和颜料混合是两回事。

def _linear_to_srgb01(c):
    """线性光 [0,1] -> sRGB 伽马 [0,1]"""
    c = np.clip(c, 0.0, 1.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * (c ** (1 / 2.4)) - 0.055)


def rgb_to_linear01(rgb):
    """sRGB 0-255 -> 线性光 [0,1]（对外暴露，供混合/评估复用）"""
    return _srgb_to_linear(np.asarray(rgb, dtype=float))


def mix_colors(rgb_list, weights):
    """
    颜料式混合：直接在 sRGB 数值上加权平均。
    rgb_list: [np.array([r,g,b]), ...]；weights 会自动归一化。
    返回混合后的 RGB (np.array, 0-255)。
    """
    weights = np.asarray(weights, dtype=float)
    weights = weights / weights.sum()
    stacked = np.vstack(rgb_list)
    return np.clip((stacked.T @ weights), 0, 255)


def partitive_mix(rgb_list, weights):
    """
    点彩式 / 光学混合：在线性光空间按面积占比加权平均，模拟人眼对离散
    颗粒的空间视觉积分。这是 EPDM 颗粒、半色调印刷等场景的正确模型。
    rgb_list: [np.array([r,g,b]), ...]（0-255）；weights 为面积占比，自动归一化。
    返回感知等效的 sRGB (np.array, 0-255)。
    """
    weights = np.asarray(weights, dtype=float)
    weights = weights / weights.sum()
    lin = np.vstack([rgb_to_linear01(c) for c in rgb_list])  # n x 3，线性光 [0,1]
    mixed_lin = weights @ lin
    return np.clip(_linear_to_srgb01(mixed_lin), 0, 1) * 255.0


def mix_hex(hex_list, weights):
    """颜料式混合若干 HEX 颜色，返回混合后的 HEX。"""
    rgb_list = [hex_to_rgb(h) for h in hex_list]
    return rgb_to_hex(mix_colors(rgb_list, weights))


def partitive_mix_hex(hex_list, weights):
    """点彩式混合若干 HEX 颜色，返回感知等效的 HEX。"""
    rgb_list = [hex_to_rgb(h) for h in hex_list]
    return rgb_to_hex(partitive_mix(rgb_list, weights))


if __name__ == "__main__":
    # 简单自测
    print("深蓝 #0B1A5C -> Lab:", np.round(hex_to_lab("#0B1A5C"), 2))
    print("目标 #07166B vs 深蓝 #0B1A5C, ΔE00 =",
          round(delta_e("#07166B", "#0B1A5C"), 2))
    mixed = mix_hex(["#0B1A5C", "#0A1428", "#1E3A8C"], [55, 30, 15])
    print("55:30:15 颜料式混合 ->", mixed, " ΔE00 vs 目标 =",
          round(delta_e("#07166B", mixed), 2))
    pmixed = partitive_mix_hex(["#0B1A5C", "#0A1428", "#1E3A8C"], [55, 30, 15])
    print("55:30:15 点彩式混合 ->", pmixed, " ΔE00 vs 目标 =",
          round(delta_e("#07166B", pmixed), 2))
    print()
    print("黑白各 50%，颜料式:", mix_hex(["#000000", "#FFFFFF"], [50, 50]),
          "  点彩式:", partitive_mix_hex(["#000000", "#FFFFFF"], [50, 50]))
