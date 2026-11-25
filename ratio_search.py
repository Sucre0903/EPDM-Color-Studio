"""
配比搜索（EPDM 点彩混色版）
============================
输入目标色，从色库搜出「用户指定数量」的颗粒配比 —— 严格按用户要求的
颜色数量出方案（不为了拟合效率自动减色），并可选加入「点缀杂色」增加
颗粒丰富度，贴近真实 EPDM 铺装效果。

核心变化（相对上一版「油漆调色」模型）：
- 评价指标从「颜料式加权平均」换成「点彩式光学混合」（partitive_mix，
  线性光空间按面积占比加权平均），这是人眼对离散颗粒空间视觉积分的
  正确物理模型，和颜料真正融合是两回事；
- 严格保留用户指定的颜色数量，每个颜色都设最低比例下限（floor），
  不会被优化器为了拟合精度而"优化掉"；
- 可选自动加入 1 种点缀杂色（浅色/深色/对比色调），贴近真实 EPDM
  地坪常见的"跳色"设计手法；
- 目标函数仍是 scipy SLSQP 连续最优化，只是换了混合模型和约束方式。
"""

import itertools
import numpy as np
import pandas as pd

from color_utils import (
    hex_to_rgb, rgb_to_hex, rgb_to_lab, hex_to_lab,
    delta_e_ciede2000, rgb_to_linear01, _linear_to_srgb01,
)

try:
    from scipy.optimize import minimize
    _HAS_SCIPY = True
except Exception:
    _HAS_SCIPY = False

TOOL_CODES = ["FK-010", "FK-011", "FK-001", "FK-060", "FK-022"]  # 炭黑/高黑/钛白/中灰/蓝黑，兼顾压暗与提亮
ACCENT_LIGHT = ["FK-001", "FK-095", "FK-061"]   # 浅色点缀池：钛白/米色/浅灰（目标偏暗时用）
ACCENT_DARK = ["FK-010", "FK-011", "FK-062"]    # 深色点缀池：炭黑/高黑/深灰（目标偏亮时用）
TOP_K = 10           # 保留离目标最近的候选色数量
N_REFINE = 80         # 预筛后进入精确最优化的组合数量
DEFAULT_FLOOR_PCT = 3.0     # 每个主色最低比例（%），保证颜色数量不被优化掉
DEFAULT_ACCENT_PCT = 5.0    # 默认点缀杂色比例（%），可在界面调节


def load_library(csv_path="data/fk_colors.csv"):
    """
    读取色库，返回 DataFrame。核心列：色号, HEX（必需）。
    可选列：名称, 备注（不存在时自动补空列，兼容旧版只有两列的色库文件）。
    """
    df = pd.read_csv(csv_path, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    df["色号"] = df["色号"].astype(str).str.strip()
    df["HEX"] = df["HEX"].astype(str).str.strip()
    for col in ("名称", "备注"):
        if col not in df.columns:
            df[col] = ""
        else:
            df[col] = df[col].fillna("").astype(str)
    return df[["色号", "HEX", "名称", "备注"]]


def import_library_table(raw_df, col_map):
    """
    把用户上传的任意表格（Excel/CSV 读出来的 DataFrame）按列映射转换成
    标准色库格式（色号, HEX, 名称, 备注）。
    col_map: {"色号": "原表格里的列名", "HEX": "...", "名称": "...", "备注": "..."}
             "名称"/"备注" 的值可以是 None 表示原表格没有对应列。
    返回标准化后的 DataFrame，以及一份问题清单（如 HEX 格式不对的行号）。
    """
    out = pd.DataFrame()
    out["色号"] = raw_df[col_map["色号"]].astype(str).str.strip()

    hex_col = col_map.get("HEX")
    if hex_col and hex_col in raw_df.columns:
        hex_vals = raw_df[hex_col].astype(str).str.strip()
    else:
        hex_vals = pd.Series([""] * len(raw_df))

    def _norm(h):
        h = str(h).strip()
        if not h or h.lower() == "nan":
            return ""
        # 兼容 "R,G,B" / "R G B" 三元组格式（常见于厂家表格）
        parts = [p for p in __import__("re").split(r"[,\s，]+", h) if p]
        if len(parts) == 3 and all(p.replace(".", "").isdigit() for p in parts):
            try:
                r, g, b = (max(0, min(255, int(float(p)))) for p in parts)
                return "#{:02X}{:02X}{:02X}".format(r, g, b)
            except Exception:
                pass
        h = h.upper()
        if not h.startswith("#"):
            h = "#" + h
        return h

    out["HEX"] = hex_vals.map(_norm)

    for field in ("名称", "备注"):
        col = col_map.get(field)
        if col and col in raw_df.columns:
            out[field] = raw_df[col].astype(str).fillna("").str.strip()
        else:
            out[field] = ""

    hex_re = __import__("re").compile(r"^#[0-9A-Fa-f]{6}$")
    problems = []
    for i, row in out.iterrows():
        if not row["色号"] or row["色号"].lower() == "nan":
            problems.append((i, "色号为空"))
        elif not hex_re.match(row["HEX"]):
            problems.append((i, f"HEX 格式不对：{row['HEX']!r}"))

    return out, problems


def _build_candidates(target_hex, df):
    target_lab = hex_to_lab(target_hex)
    records = df.to_dict("records")
    for r in records:
        r["_de"] = delta_e_ciede2000(target_lab, hex_to_lab(r["HEX"]))
    records.sort(key=lambda r: r["_de"])

    pool = records[:TOP_K]
    codes = {r["色号"] for r in pool}
    for r in records:
        if r["色号"] in TOOL_CODES and r["色号"] not in codes:
            pool.append(r)
            codes.add(r["色号"])
    return pool, records


def _pick_accent(target_lab, all_records, exclude_codes):
    """
    选一个「点缀杂色」，遵循真实 EPDM 设计常见做法：
    目标偏暗 -> 配浅色点缀（钛白/米色/浅灰），目标偏亮 -> 配深色点缀（炭黑/深灰）。
    这样既有视觉层次，又不会因为点缀色和目标色反差过大而大幅拉低拟合精度。
    在对应池子里选与目标「色相」差异最大的一个，兼顾层次感和合理性。
    """
    L = target_lab[0]
    prefer_codes = ACCENT_LIGHT if L < 50 else ACCENT_DARK
    pool = [r for r in all_records
           if r["色号"] in prefer_codes and r["色号"] not in exclude_codes]
    if not pool:
        pool = [r for r in all_records if r["色号"] not in exclude_codes]
    if not pool:
        return None
    # 池内选「离目标最近」的一个：浅/深类别本身已经提供了层次对比，
    # 没必要在同类别里再选最伤拟合精度的极端选项。
    return min(pool, key=lambda r: delta_e_ciede2000(target_lab, hex_to_lab(r["HEX"])))


def _partitive_lab(weights_frac, A_lin, accent_lin=None, accent_frac=0.0):
    """按线性光加权平均算混合结果的 Lab（供目标函数复用）。"""
    mixed_lin = weights_frac @ A_lin
    if accent_lin is not None and accent_frac > 0:
        mixed_lin = mixed_lin * (1 - accent_frac) + accent_lin * accent_frac
    mixed_srgb = np.clip(_linear_to_srgb01(mixed_lin), 0, 1) * 255.0
    return rgb_to_lab(mixed_srgb), mixed_srgb


def _optimize_main_weights(main_A_lin, target_lab, floor_frac,
                           accent_lin=None, accent_frac=0.0, warm=None):
    """
    优化「主色」之间的相对权重（simplex，和为 1），accent 权重固定在外部。
    floor_frac：每个主色相对权重下限（如 0.05 表示至少占主色部分的 5%）。
    返回 (weights_frac, delta_e)。
    """
    n = len(main_A_lin)

    def obj(w):
        w = np.clip(w, 0, None)
        s = w.sum()
        if s <= 0:
            return 1e6
        w = w / s
        lab, _ = _partitive_lab(w, main_A_lin, accent_lin, accent_frac)
        return delta_e_ciede2000(lab, target_lab)

    if n == 1:
        w = np.array([1.0])
        return w, obj(w)

    if _HAS_SCIPY:
        cons = ({"type": "eq", "fun": lambda w: w.sum() - 1.0},)
        hi = max(floor_frac, 1.0 - floor_frac * (n - 1))
        bnds = [(floor_frac, hi)] * n
        starts = [np.full(n, 1.0 / n)]
        if warm is not None:
            w0 = np.clip(np.asarray(warm, dtype=float), floor_frac, None)
            starts.append(w0 / w0.sum())

        best_w, best_de = None, 1e9
        for s0 in starts:
            try:
                res = minimize(obj, s0, method="SLSQP", bounds=bnds,
                               constraints=cons,
                               options={"maxiter": 150, "ftol": 1e-6})
                w = np.clip(res.x, 0, None)
                if w.sum() <= 0:
                    continue
                w = w / w.sum()
                de = obj(w)
                if de < best_de:
                    best_w, best_de = w, de
            except Exception:
                continue
        if best_w is not None:
            return best_w, best_de

    # 退化方案：网格搜索
    best_w, best_de = None, 1e9
    step = 20
    for combo in _simplex_grid(n, step):
        w = np.array(combo, dtype=float) / step
        if (w < floor_frac - 1e-9).any():
            continue
        de = obj(w)
        if de < best_de:
            best_w, best_de = w, de
    if best_w is None:
        best_w = np.full(n, 1.0 / n)
        best_de = obj(best_w)
    return best_w, best_de


def _simplex_grid(n, total):
    if n == 1:
        yield (total,)
        return
    for i in range(total + 1):
        for rest in _simplex_grid(n - 1, total - i):
            yield (i,) + rest


def _quick_estimate(main_A_lin, target_lin):
    """线性光空间最小二乘快速粗估，仅用于组合预筛排序。"""
    n = len(main_A_lin)
    lam = 4.0
    M = np.vstack([main_A_lin.T, np.sqrt(lam) * np.ones((1, n))])
    b = np.concatenate([target_lin, [np.sqrt(lam)]])
    try:
        w, *_ = np.linalg.lstsq(M, b, rcond=None)
    except Exception:
        w = np.full(n, 1.0 / n)
    w = np.clip(w, 0, None)
    if w.sum() <= 0:
        w = np.full(n, 1.0 / n)
    return w / w.sum()


def _round_to_100(codes, hexes, pct_array):
    """把浮点百分比数组四舍五入成整数且和为 100（最大余数法）。"""
    pct_array = np.asarray(pct_array, dtype=float)
    floor_vals = np.floor(pct_array).astype(int)
    remainder = 100 - int(floor_vals.sum())
    order = np.argsort(-(pct_array - floor_vals))  # 按小数部分从大到小补
    ipct = floor_vals.copy()
    for i in range(remainder):
        ipct[order[i % len(order)]] += 1
    comps = []
    for c, h, p in zip(codes, hexes, ipct):
        if p <= 0:
            continue
        comps.append({"色号": c, "HEX": h, "比例": int(p)})
    comps.sort(key=lambda x: -x["比例"])
    return comps


def _plan_code_set(plan):
    return frozenset(c["色号"] for c in plan["components"])


def _plan_ratio_map(plan):
    return {c["色号"]: c["比例"] for c in plan["components"]}


def _plan_distance(a, b):
    """
    两个方案之间的「结构差异度」（0~1，越大越不像）：
    - 用色集合的 Jaccard 距离（权重最高，颜色组合本身不同才是真正的
      "配色思路不同"，而不是同一批颜色调了调比例）；
    - 颜色数量是否不同（数量不同本身就是一种明显的结构差异）；
    - 共同颜色的比例差异（哪怕用的颜色一样，主次比例结构差异大也算数）。
    """
    sa, sb = _plan_code_set(a), _plan_code_set(b)
    union = sa | sb
    inter = sa & sb
    jaccard_dist = 1.0 - (len(inter) / len(union) if union else 1.0)
    count_diff = 0.0 if len(sa) == len(sb) else 1.0
    if inter:
        ra, rb = _plan_ratio_map(a), _plan_ratio_map(b)
        ratio_diff = sum(abs(ra[c] - rb[c]) for c in inter) / len(inter) / 100.0
    else:
        ratio_diff = 1.0
    return 0.55 * jaccard_dist + 0.25 * count_diff + 0.2 * ratio_diff


def _select_diverse_plans(results, k=6, de_margin=4.0, min_distance=0.12):
    """
    从已经算好、按 ΔE00 升序排列的 results 里（不重新搜索，零额外计算
    开销），用「贪心最远点采样」挑出最多 k 个配色结构有明显区别的方案：
    - 第一个固定是全局最优解（Best Match）；
    - 之后每一轮都选「和已选方案集合最不像的」那个，保证方案之间彼此
      有真实区别，而不是全部挤在最优解附近做几个比例的小调整；
    - 只在 ΔE00 <= 最优解 + de_margin 的范围内挑选，保证每个方案对目标色
      的还原度都过关；
    - 如果剩下的候选和已选方案都太像（距离 < min_distance），提前停止，
      不会为了凑数硬塞进去两个几乎一样的方案。
    """
    if not results:
        return []
    best_de = results[0]["delta_e"]
    pool = [r for r in results if r["delta_e"] <= best_de + de_margin]
    if not pool:
        pool = results[:1]

    selected = [pool[0]]
    remaining = pool[1:]
    while remaining and len(selected) < k:
        best_r, best_score = None, -1.0
        for r in remaining:
            score = min(_plan_distance(r, s) for s in selected)
            if score > best_score:
                best_score = score
                best_r = r
        if best_r is None or best_score < min_distance:
            break
        selected.append(best_r)
        remaining.remove(best_r)
    return selected


def search_recipe(target_hex, total_weight=100.0, material="EPDM",
                  n_colors=4, use_accent=True, accent_pct=DEFAULT_ACCENT_PCT,
                  floor_pct=DEFAULT_FLOOR_PCT, locked_codes=None,
                  excluded_codes=None, explore_mode=False,
                  csv_path="data/fk_colors.csv"):
    """
    主入口。n_colors 为「严格使用」的颗粒颜色数量（不会被自动减少）。
    use_accent=True 且 n_colors>=3 时，会自动选 1 种点缀杂色。
    locked_codes：必须强制参与配比的色号列表（如设计师指定的品牌色/库存色），
                 不会被算法剔除；若数量超过 n_colors 可容纳的主色数，
                 会自动扩容颜色数量以容纳所有锁定色（并在返回值里说明）。
    excluded_codes：本次搜索完全不考虑的色号列表（如某些颗粒缺货/工厂
                   临时不想用），效果等价于把这些颜色暂时从色库里拿掉；
                   若某色号同时出现在 locked_codes 和 excluded_codes 里，
                   按锁定处理（锁定的约束更明确）。
    explore_mode：False（默认，Best Match）只返回 1 个最优方案；
                 True（Explore，方案探索）在色差可接受范围内，从已经
                 算好的候选池里用多样性筛选挑出最多 6 个配色结构有明显
                 区别的方案（不同颜色组合/数量/主次比例结构），而不是
                 简单调亮度——不需要额外的搜索开销。
    返回 dict：target_hex / total_weight / material / n_colors /
              used_accent / accent_code / locked_codes / excluded_codes /
              notes / recommended / explore。
              explore 是一个列表，Best Match 模式下只有 1 项（和
              recommended 相同）；Explore 模式下有多项，第一项就是
              recommended，后面是多样性筛选出的其它方案。
    """
    n_colors = int(max(2, min(6, n_colors)))
    accent_pct = float(max(0.0, min(accent_pct, 30.0)))
    notes = []

    df_full = load_library(csv_path)
    lib_codes = set(df_full["色号"])

    locked_codes = [c.strip() for c in (locked_codes or []) if c and c.strip()]
    valid_locked, invalid_locked = [], []
    for c in locked_codes:
        (valid_locked if c in lib_codes else invalid_locked).append(c)
    if invalid_locked:
        notes.append(f"锁定色号未在色库中找到，已忽略：{', '.join(invalid_locked)}")
    locked_codes = list(dict.fromkeys(valid_locked))  # 去重保序
    MAX_LOCKED = 8
    if len(locked_codes) > MAX_LOCKED:
        notes.append(f"锁定色号过多，仅保留前 {MAX_LOCKED} 个：{', '.join(locked_codes[:MAX_LOCKED])}")
        locked_codes = locked_codes[:MAX_LOCKED]

    excluded_codes = [c.strip() for c in (excluded_codes or []) if c and c.strip()]
    valid_excluded, invalid_excluded = [], []
    for c in excluded_codes:
        (valid_excluded if c in lib_codes else invalid_excluded).append(c)
    if invalid_excluded:
        notes.append(f"排除色号未在色库中找到，已忽略：{', '.join(invalid_excluded)}")
    excluded_codes = list(dict.fromkeys(valid_excluded))
    conflict = set(excluded_codes) & set(locked_codes)
    if conflict:
        notes.append(f"以下色号同时被锁定和排除，按锁定处理：{', '.join(sorted(conflict))}")
        excluded_codes = [c for c in excluded_codes if c not in conflict]

    df = df_full[~df_full["色号"].isin(excluded_codes)].reset_index(drop=True) \
        if excluded_codes else df_full

    candidates, all_records = _build_candidates(target_hex, df)
    target_lab = hex_to_lab(target_hex)
    target_lin = rgb_to_linear01(hex_to_rgb(target_hex))

    # ---- 是否加入点缀杂色（排除已被锁定的色号，避免重复计入）----
    accent = None
    if use_accent and n_colors >= 3:
        accent = _pick_accent(target_lab, all_records, exclude_codes=set(locked_codes))
    accent_frac = (accent_pct / 100.0) if accent else 0.0
    accent_lin = rgb_to_linear01(hex_to_rgb(accent["HEX"])) if accent else None

    # ---- 颜色数量：若锁定色数量超出可用主色槽位，自动扩容 ----
    n_main_needed_for_locks = len(locked_codes)
    n_main = n_colors - (1 if accent else 0)
    if n_main_needed_for_locks > n_main:
        n_main = n_main_needed_for_locks
        n_colors_new = n_main + (1 if accent else 0)
        if n_colors_new != n_colors:
            notes.append(f"锁定颜色数量较多，颜色数量已自动从 {n_colors} 扩容到 {n_colors_new}")
        n_colors = n_colors_new

    all_records_by_code = {r["色号"]: r for r in all_records}
    locked_records = [all_records_by_code[c] for c in locked_codes]

    main_pool = [c for c in candidates
                if c["色号"] not in locked_codes
                and (accent is None or c["色号"] != accent["色号"])]
    n_free = max(0, n_main - len(locked_records))
    n_use_free = min(n_free, len(main_pool))
    floor_pct = float(max(0.5, min(floor_pct, 100.0 / max(n_main, 1) - 0.5)))
    floor_frac = floor_pct / 100.0

    # ---- 预筛：线性光最小二乘粗估，排序后只精算前 N_REFINE 个组合 ----
    prescreen = []
    free_combos = (list(itertools.combinations(main_pool, n_use_free))
                  if n_use_free > 0 else [()])
    for free_combo in free_combos:
        combo = tuple(locked_records) + free_combo
        A_lin = np.vstack([rgb_to_linear01(hex_to_rgb(c["HEX"])) for c in combo])
        # 粗估时先扣掉点缀杂色的固定贡献，再拟合剩余部分
        residual_target = (target_lin - accent_frac * (accent_lin if accent_lin is not None else 0)) \
                          / max(1e-6, 1 - accent_frac)
        w_est = _quick_estimate(A_lin, residual_target)
        lab_est, _ = _partitive_lab(w_est, A_lin, accent_lin, accent_frac)
        de_est = delta_e_ciede2000(lab_est, target_lab)
        prescreen.append((de_est, combo, A_lin, w_est))
    prescreen.sort(key=lambda x: x[0])

    results = []
    for de_est, combo, A_lin, w_est in prescreen[:N_REFINE]:
        codes = [c["色号"] for c in combo]
        hexes = [c["HEX"] for c in combo]
        w, de = _optimize_main_weights(A_lin, target_lab, floor_frac,
                                       accent_lin, accent_frac, warm=w_est)
        lab, mixed_srgb = _partitive_lab(w, A_lin, accent_lin, accent_frac)
        mixed_hex = rgb_to_hex(mixed_srgb)
        mixed_L = lab[0]

        # 主色绝对百分比 + 点缀杂色绝对百分比
        main_pct = w * (1 - accent_frac) * 100
        all_codes = list(codes)
        all_hexes = list(hexes)
        all_pct = list(main_pct)
        if accent:
            all_codes.append(accent["色号"])
            all_hexes.append(accent["HEX"])
            all_pct.append(accent_frac * 100)

        comps = _round_to_100(all_codes, all_hexes, all_pct)
        if not comps:
            continue
        results.append({
            "components": comps,
            "mixed_hex": mixed_hex,
            "delta_e": round(float(de), 2),
            "mixed_L": float(mixed_L),
        })

    if not results:
        raise ValueError("未能生成配比，请检查目标色、色库或参数设置。")

    results.sort(key=lambda r: r["delta_e"])
    recommended = results[0]

    if explore_mode:
        explore = _select_diverse_plans(results, k=6)
    else:
        explore = [recommended]

    def _attach_weight(plan):
        if plan is None:
            return None
        for c in plan["components"]:
            c["用量kg"] = round(total_weight * c["比例"] / 100.0, 3)
        return plan

    return {
        "target_hex": target_hex.upper(),
        "total_weight": total_weight,
        "material": material,
        "n_colors": n_colors,
        "used_accent": accent is not None,
        "accent_code": accent["色号"] if accent else None,
        "locked_codes": locked_codes,
        "excluded_codes": excluded_codes,
        "explore_mode": explore_mode,
        "notes": notes,
        "recommended": _attach_weight(recommended),
        "explore": [_attach_weight(p) for p in explore],
    }


def format_plan_text(plan, title):
    if plan is None:
        return f"【{title}】无\n"
    lines = [f"【{title}】感知混合 {plan['mixed_hex']}  ΔE00(点彩)={plan['delta_e']}"]
    for c in plan["components"]:
        lines.append(f"    {c['色号']}  {c['比例']:>3}%  {c['用量kg']:>7} kg")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    import time
    print("=== 严格 4 色 + 点缀杂色 ===")
    t = time.time()
    res = search_recipe("#07166B", 100, "EPDM", n_colors=4, use_accent=True)
    print(f"(耗时 {time.time()-t:.2f}s)  点缀色: {res['accent_code']}")
    print(format_plan_text(res["recommended"], "推荐"), end="")

    print("\n=== 严格 5 色，不加点缀 ===")
    res2 = search_recipe("#07166B", 100, "EPDM", n_colors=5, use_accent=False)
    print(format_plan_text(res2["recommended"], "推荐"), end="")

    print("\n=== 颜色数量校验（是否都出现且不低于下限）===")
    for n in (2, 3, 4, 5, 6):
        r = search_recipe("#EC7539", 100, "EPDM", n_colors=n, use_accent=True)
        pct_list = [c["比例"] for c in r["recommended"]["components"]]
        print(f"n_colors={n} -> 实际出现 {len(pct_list)} 色，比例 {pct_list}，"
              f"点缀={r['accent_code']}")

    print("\n--- 其它色系抽测 (4色+点缀) ---")
    for tgt in ["#C0392B", "#2ECC71", "#F1C40F", "#7D3C98"]:
        r = search_recipe(tgt, 100, "EPDM", n_colors=4, use_accent=True)
        p = r["recommended"]
        s = " + ".join(f"{c['色号']}{c['比例']}%" for c in p["components"])
        print(f"{tgt} -> {p['mixed_hex']} ΔE={p['delta_e']} | {s}")
