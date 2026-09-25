"""手牌/结算图片渲染（PIL，文泉驿正黑）。自适应宽度，不裁切。"""
from __future__ import annotations

import os
import tempfile
from typing import List, Optional, Sequence

from PIL import Image, ImageDraw, ImageFont

from ..engine import cards as C

FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",           # 文泉驿正黑（Debian/Ubuntu）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",  # Noto CJK
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",                      # macOS
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",         # 兜底
)
_resolved_font: str = ""

# 画布参数
CARD_W, CARD_H = 96, 134
CARD_R = 12
GAP = 8
PAD = 24
MAX_IMG_W = 900              # 图片最大宽度（超出自动减少每行张数）
BG = (26, 115, 72)          # 牌桌绿
CARD_BG = (255, 255, 255)
CARD_EDGE = (188, 194, 204)
RED = (200, 30, 30)
BLACK = (30, 30, 30)
GOLD = (214, 158, 46)
WILD_BG = (255, 246, 214)

_font_cache = {}


def _font_path() -> str:
    global _resolved_font
    if not _resolved_font:
        for p in FONT_CANDIDATES:
            if os.path.exists(p):
                _resolved_font = p
                break
    return _resolved_font


def _font(size: int) -> ImageFont.FreeTypeFont:
    if size not in _font_cache:
        fp = _font_path()
        try:
            _font_cache[size] = ImageFont.truetype(fp, size) if fp else ImageFont.load_default()
        except Exception:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    try:
        return int(draw.textlength(str(text), font=font))
    except Exception:
        return len(str(text)) * 14


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> List[str]:
    """按像素宽度自动折行（中英混排逐字测量）。"""
    text = str(text)
    if _text_w(draw, text, font) <= max_w:
        return [text]
    out: List[str] = []
    cur = ""
    for ch in text:
        if _text_w(draw, cur + ch, font) <= max_w:
            cur += ch
        else:
            if cur:
                out.append(cur)
            cur = ch
    if cur:
        out.append(cur)
    return out or [""]


def _measure_ctx() -> tuple:
    img = Image.new("RGB", (8, 8), BG)
    return img, ImageDraw.Draw(img)


def _draw_card(draw: ImageDraw.ImageDraw, x: int, y: int, card: str, wild: bool):
    """画一张牌。card: '♠3' / '小王' / '大王'"""
    x2, y2 = x + CARD_W, y + CARD_H
    fill = WILD_BG if wild else CARD_BG
    draw.rounded_rectangle([x, y, x2, y2], radius=CARD_R, fill=fill,
                           outline=GOLD if wild else CARD_EDGE, width=3 if wild else 2)
    if card in (C.JOKER_SMALL, C.JOKER_BIG):
        big = card == C.JOKER_BIG
        txt = "大\n王" if big else "小\n王"
        f = _font(40)
        color = RED if big else BLACK
        draw.multiline_text((x + CARD_W // 2, y + CARD_H // 2 + 6), txt, font=f,
                            fill=color, anchor="mm", align="center", spacing=2)
        if wild:
            draw.rectangle([x + 4, y + 4, x + 26, y + 26], outline=GOLD, width=2)
        return
    suit = C.card_suit(card) or ""
    rank = C.card_rank(card)
    color = RED if suit in ("♥", "♦") else BLACK
    # 左上角：点数 + 花色（大号）
    fr = _font(40 if len(rank) > 1 else 46)
    draw.text((x + 10, y + 8), rank, font=fr, fill=color)
    fs = _font(34)
    draw.text((x + 10, y + 56), suit, font=fs, fill=color)
    # 中央大花色
    fc = _font(56)
    draw.text((x + CARD_W * 0.62, y + CARD_H * 0.62), suit, font=fc, fill=color, anchor="mm")
    if wild:
        fw = _font(22)
        draw.rounded_rectangle([x + CARD_W - 40, y + CARD_H - 34, x + CARD_W - 8, y + CARD_H - 8],
                               radius=6, fill=GOLD)
        draw.text((x + CARD_W - 24, y + CARD_H - 21), "癞", font=fw, fill=(255, 255, 255), anchor="mm")


def render_hand(hand: Sequence[str], wild_rank: Optional[str] = None,
                title: str = "", subtitle: str = "") -> str:
    """渲染手牌为 PNG，返回文件路径（调用方负责删除）。宽度自适应，永不超框。"""
    cards = C.sort_cards(hand)
    n = len(cards)
    per_row = max(1, min(max(n, 1), (MAX_IMG_W - PAD * 2 + GAP) // (CARD_W + GAP)))
    rows = (n + per_row - 1) // per_row or 1
    head_h = 0
    if title:
        head_h += 50
    if subtitle:
        head_h += 38
    footer_h = 44

    probe_img, probe = _measure_ctx()
    f_title = _font(34)
    f_sub = _font(24)
    need_w = PAD * 2
    if title:
        need_w = max(need_w, _text_w(probe, title, f_title) + PAD * 2)
    if subtitle:
        need_w = max(need_w, _text_w(probe, subtitle, f_sub) + PAD * 2)
    grid_w = PAD * 2 + per_row * CARD_W + (per_row - 1) * GAP
    w = max(need_w, grid_w)
    h = PAD * 2 + head_h + rows * CARD_H + (rows - 1) * GAP + footer_h

    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)

    y = PAD
    if title:
        draw.text((w // 2, y + 8), title, font=f_title, fill=(255, 255, 255), anchor="ma")
        y += 50
    if subtitle:
        draw.text((w // 2, y + 6), subtitle, font=f_sub, fill=(235, 240, 235), anchor="ma")
        y += 38

    for i, card in enumerate(cards):
        r, c = divmod(i, per_row)
        x = PAD + c * (CARD_W + GAP)
        yy = y + r * (CARD_H + GAP)
        wild = wild_rank is not None and C.card_rank(card) == wild_rank
        _draw_card(draw, x, yy, card, wild)

    f = _font(24)
    note = f"共 {n} 张"
    if wild_rank:
        note += f"　癞子：{wild_rank}"
    draw.text((w // 2, h - PAD - 10), note, font=f, fill=(235, 240, 235), anchor="md")

    fd, path = tempfile.mkstemp(prefix="ddz_hand_", suffix=".png", dir="/tmp")
    os.close(fd)
    img.save(path, "PNG")
    return path


def render_result(title: str, lines: Sequence[str],
                  max_width: int = MAX_IMG_W, min_width: int = 420) -> str:
    """渲染结算图：宽度自适应 + 超长自动折行（不再出现固定宽度裁切）。"""
    f_t = _font(38)
    f_b = _font(28)
    line_h = 46
    probe_img, probe = _measure_ctx()
    inner_max = max_width - PAD * 2 - 16

    wrapped: List[str] = []
    for ln in lines:
        wrapped.extend(_wrap(probe, ln, f_b, inner_max))

    w = min_width
    w = max(w, _text_w(probe, title, f_t) + PAD * 2)
    for ln in wrapped:
        w = max(w, _text_w(probe, ln, f_b) + PAD * 2 + 16)
    w = min(w, max_width)

    h = PAD * 2 + 62 + max(1, len(wrapped)) * line_h
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    draw.text((w // 2, PAD + 8), title, font=f_t, fill=(255, 255, 255), anchor="ma")
    y = PAD + 70
    for line in wrapped:
        draw.text((PAD + 8, y), line, font=f_b, fill=(240, 246, 240))
        y += line_h
    fd, path = tempfile.mkstemp(prefix="ddz_result_", suffix=".png", dir="/tmp")
    os.close(fd)
    img.save(path, "PNG")
    return path


def cleanup(path: Optional[str]):
    if path and os.path.exists(path):
        try:
            os.unlink(path)
        except OSError:
            pass
