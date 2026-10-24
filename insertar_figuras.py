# -*- coding: utf-8 -*-
"""Inserta, en la pagina donde el texto menciona "Figure N", la imagen de esa figure."""
import os
import re
import sys

import numpy as np
import fitz
from PIL import Image

SRC = "the-wave-principle.pdf"
DST = "el principio de la onda - con figures en linea.pdf"
FIG_DIR = "figures_extraidas"
DPI_FIG = 170          # resolucion al recortar la figure desde su pagina
MAX_W, MAX_H = 230.0, 280.0   # tamano maximo (pt) del thumbnail insertado
MIN_W, MIN_H = 55.0, 45.0     # tamano minimo; si no cabe, se omite
PAD = 10.0             # margen interior del recuadro
MARGIN = 28.0          # zona util de la pagina (no colocar en los bordes)


def prose_lines(page):
    """Lineas del texto con al menos 8 palabras (para distinguir pagina de texto)."""
    n = 0
    for ln in page.get_text().replace("\xa0", " ").split("\n"):
        words = [w for w in re.split(r"\s+", ln) if re.search(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]", w)]
        if len(words) >= 8:
            n += 1
    return n


def overlays(page):
    """Imagenes embebidas que no cubren toda la pagina (figures destacadas)."""
    out = []
    for img in page.get_images(full=True):
        xref = img[0]
        for r in page.get_image_rects(xref):
            if not (r.width >= 590 and r.height >= 770):
                out.append((xref, r))
    return out


def content_bbox(page, dpi=60):
    """Recuadro (fitz) del contenido visible de la pagina."""
    pix = page.get_pixmap(dpi=dpi)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    g = a[:, :, 0] if pix.n < 3 else a[:, :, :3].mean(axis=2)
    mask = g < 235
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return None
    s = 72.0 / dpi
    return fitz.Rect(xs.min() * s, ys.min() * s, (xs.max() + 1) * s, (ys.max() + 1) * s)


def build_fig_map(doc):
    caps = {}
    for i, p in enumerate(doc):
        for ln in p.get_text().replace("\xa0", " ").split("\n"):
            s = ln.strip()
            m = re.fullmatch(r"Figure\s*(\d+)", s)
            if m:
                caps.setdefault(int(m.group(1)), []).append(i)

    figmap, last = {}, 0
    for n in range(1, 80):
        if n not in caps:
            print("AVISO: sin caption para Figure", n)
            continue
        cap = caps[n][0]
        if n > 1 and caps.get(n - 1) and cap == caps[n - 1][0]:
            figmap[n] = figmap[n - 1]           # dos figures en la misma plancha
            continue
        cands = [c for c in (cap - 1, cap) if c > last]
        pick = None
        for c in cands:
            if overlays(doc[c]):
                pick = c
                break
        if pick is None:
            pick = cands[0] if cands else cap
        figmap[n] = pick
        last = pick
    return figmap, caps


def render_figure(doc, page_index, out_png, clip=None, dpi=DPI_FIG):
    page = doc[page_index]
    pix = page.get_pixmap(dpi=dpi, clip=clip)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    a = np.asarray(img.convert("L"))
    mask = a < 245
    ys, xs = np.where(mask)
    if len(xs):
        pad = 8
        x0, x1 = max(0, xs.min() - pad), min(img.width, xs.max() + 1 + pad)
        y0, y1 = max(0, ys.min() - pad), min(img.height, ys.max() + 1 + pad)
        img = img.crop((x0, y0, x1, y1))
    img.save(out_png, optimize=True)
    return out_png


def split_half_clip(page, clip):
    """Divide la plancha en dos mitades (figures 22 y 23 comparten pagina)."""
    pix = page.get_pixmap(dpi=60, clip=clip)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    g = a[:, :, 0] if pix.n < 3 else a[:, :, :3].mean(axis=2)
    col = (g < 235).sum(axis=0)
    w = pix.width
    lo, hi = int(w * 0.30), int(w * 0.70)
    band = col[lo:hi]
    gap = int(np.argmax(band == 0)) + lo if (band == 0).any() else w // 2
    s = clip.width / w
    mid = clip.x0 + gap * s
    return fitz.Rect(clip.x0, clip.y0, mid, clip.y1), fitz.Rect(mid, clip.y0, clip.x1, clip.y1)


def largest_empty_rect(ink):
    """Mayor rectangulo sin tinta (matriz booleana). Devuelve (r0, c0, r1, c1)."""
    h, w = ink.shape
    heights = np.zeros(w, dtype=int)
    best = (0, 0, 0, 0)
    best_area = 0
    for r in range(h):
        heights = np.where(ink[r], 0, heights + 1)
        stack = []
        for c in range(w + 1):
            cur = heights[c] if c < w else 0
            start = c
            while stack and stack[-1][1] >= cur:
                sc, sh = stack.pop()
                area = sh * (c - sc)
                if area > best_area:
                    best_area = area
                    best = (r - sh + 1, sc, r + 1, c)
                start = sc
            stack.append((start, cur))
    return best


def free_rects(ink, count=1):
    """Devuelve hasta `count` recuadros libres (fitz.Rect a 72 dpi), sin solaparse.

    Se recorta la zona de bordes (MARGIN) para no sangrar fuera del texto.
    """
    m = int(MARGIN)
    sub = ink[m:ink.shape[0] - m, m:ink.shape[1] - m]
    rects = []
    work = sub.copy()
    for _ in range(count):
        r0, c0, r1, c1 = largest_empty_rect(work)
        if r1 <= r0 or c1 <= c0:
            break
        rects.append(fitz.Rect(c0 + m, r0 + m, c1 + m, r1 + m))
        work[r0:r1, c0:c1] = True
    return rects


def _fit_size(bw, bh, aspect):
    """Tamano (w, h) con el que cabe una imagen de ratio `aspect` en un hueco."""
    w = min(bw, bh * aspect)
    h = w / aspect
    if h > bh:
        h = bh
        w = h * aspect
    return w, h


def pick_target(ink, aspect):
    """Elige el mejor hueco: el que deja la figure mas grande (prefiriendo abajo)."""
    cands = []
    for r in free_rects(ink, count=10):
        bw, bh = r.width - 2 * PAD, r.height - 2 * PAD - 12
        if bw < MIN_W * 0.7 or bh < MIN_H * 0.7:
            continue
        w, h = _fit_size(bw, bh, aspect)
        if w < MIN_W * 0.7 or h < MIN_H * 0.7:
            continue
        cands.append((w * h, r.y0, r))
    if not cands:
        return None
    best_area = max(c[0] for c in cands)
    big = [c for c in cands if c[0] >= 0.6 * best_area]
    big.sort(key=lambda c: (c[0], c[1]))   # mayor figure; a igualdad, mas abajo
    return big[-1][2]


def page_ink(page, dpi=72):
    pix = page.get_pixmap(dpi=dpi)
    a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    g = a[:, :, 0] if pix.n < 3 else a[:, :, :3].mean(axis=2)
    ink = g < 215
    # dilatar 4 px para no pegar el thumbnail al texto
    d = ink.copy()
    for dy in range(-4, 5):
        for dx in range(-4, 5):
            d |= np.roll(np.roll(ink, dy, axis=0), dx, axis=1)
    return d


def place_image(page, png, target, fig_n):
    """Coloca el thumbnail de la figure dentro de `target` (fitz.Rect)."""
    iw, ih = Image.open(png).size
    aspect = iw / ih
    box = fitz.Rect(target)
    box = box & page.rect
    bw, bh = box.width - 2 * PAD, box.height - 2 * PAD - 12  # 12 pt para el pie
    if bw < MIN_W * 0.7 or bh < MIN_H * 0.7:
        return None
    w, h = _fit_size(bw, bh, iw / ih)
    sc = min(1.0, MAX_W / w, MAX_H / h)
    w, h = w * sc, h * sc
    if w < MIN_W * 0.7 or h < MIN_H * 0.7:
        return None
    x0 = box.x0 + (box.width - w) / 2
    y0 = box.y0 + (box.height - h - 12) / 2
    frame = fitz.Rect(x0 - PAD, y0 - PAD, x0 + w + PAD, y0 + h + 12 + PAD)
    page.draw_rect(frame, color=(0.55, 0.55, 0.55), fill=(1, 1, 1), width=0.6)
    page.insert_image(fitz.Rect(x0, y0, x0 + w, y0 + h), filename=png)
    page.insert_textbox(
        fitz.Rect(frame.x0, y0 + h + 2, frame.x1, frame.y1),
        "Figure %d" % fig_n, fontname="helv", fontsize=9,
        color=(0, 0, 0), align=fitz.TEXT_ALIGN_CENTER,
    )
    return frame


def trim_caption(page, clip, nums):
    """Quita de `clip` el pie impreso 'Figure N' si queda pegado al borde."""
    for n in nums:
        for cr in page.search_for("Figure %d" % n):
            if cr.width > 90 or not clip.intersects(cr):
                continue
            if cr.y0 >= clip.y0 - 2 and cr.y1 <= clip.y0 + 45:
                clip = fitz.Rect(clip.x0, min(clip.y1 - 20, cr.y1 + 6), clip.x1, clip.y1)
            elif cr.y1 <= clip.y1 + 2 and cr.y0 >= clip.y1 - 45:
                clip = fitz.Rect(clip.x0, clip.y0, clip.x1, max(clip.y0 + 20, cr.y0 - 6))
    return clip


def main():
    dry = "--map" in sys.argv
    doc = fitz.open(SRC)
    figmap, caps = build_fig_map(doc)

    os.makedirs(FIG_DIR, exist_ok=True)
    shared = {}
    for n, pg in figmap.items():
        shared.setdefault(pg, []).append(n)

    clips = {}
    for pg, nums in sorted(shared.items()):
        page = doc[pg]
        figs_ov = [(x, r) for x, r in overlays(page)
                   if r.width < 0.95 * page.rect.width]
        if prose_lines(page) >= 3 and figs_ov:
            # pagina mixta (texto + figure): usar solo las imagenes destacadas
            clip = figs_ov[0][1]
            for _, r in figs_ov[1:]:
                clip |= r
            clip &= page.rect
        else:
            clip = content_bbox(page) or page.rect
        clip = trim_caption(page, clip, nums)
        if len(nums) == 1:
            clips[nums[0]] = (pg, clip)
        else:
            left, right = split_half_clip(page, clip)
            for i, n in enumerate(sorted(nums)):
                clips[n] = (pg, left if i == 0 else right)

    print("== Mapa de figures ==")
    for n in sorted(clips):
        pg, clip = clips[n]
        print("Figure %-3d -> pagina %-4d clip %s" % (n, pg, tuple(round(v) for v in clip)))

    if dry:
        return

    pngs = {}
    for n, (pg, clip) in clips.items():
        png = os.path.join(FIG_DIR, "figure_%02d.png" % n)
        render_figure(doc, pg, png, clip=clip)
        pngs[n] = png

    # referencias en el texto
    refs = {}
    for i, p in enumerate(doc):
        for ln in p.get_text().replace("\xa0", " ").split("\n"):
            s = ln.strip()
            if re.fullmatch(r"Figure\s*\d+", s):
                continue
            for m in re.finditer(r"[Ff]igura\s*(\d+)", s):
                refs.setdefault(int(m.group(1)), []).append(i)

    plan = []
    for n in sorted(refs):
        if n not in pngs:
            print("AVISO: referencian la Figure %d pero no existe (paginas %s)" % (n, refs[n]))
            continue
        plate = clips[n][0]
        for pg in sorted(set(refs[n])):
            if pg == plate:
                continue
            plan.append((pg, n))

    from collections import defaultdict
    by_page = defaultdict(list)
    for pg, n in plan:
        by_page[pg].append(n)

    print("== Inserciones (%d figures en %d paginas) ==" % (len(plan), len(by_page)))
    for pg in sorted(by_page):
        print("pagina %-4d: Figures %s" % (pg, by_page[pg]))

    for pg in sorted(by_page):
        nums = by_page[pg]
        page = doc[pg]
        ink = page_ink(page)
        for n in nums:
            iw, ih = Image.open(pngs[n]).size
            target = pick_target(ink, iw / ih)
            if target is None:
                print("AVISO: sin espacio para Figure %d en pagina %d" % (n, pg))
                continue
            frame = place_image(page, pngs[n], target, n)
            if frame is None:
                print("AVISO: no cabio Figure %d en pagina %d" % (n, pg))
                continue
            print("  p%-4d +Figure %-3d en %s" % (pg, n, tuple(round(v) for v in frame)))
            # marcar la zona colocada como ocupada para la siguiente figure
            x0 = max(0, int(frame.x0) - 4)
            y0 = max(0, int(frame.y0) - 4)
            x1 = min(ink.shape[1], int(frame.x1) + 4)
            y1 = min(ink.shape[0], int(frame.y1) + 4)
            ink[y0:y1, x0:x1] = True

    doc.save(DST, garbage=3, deflate=True)
    doc.close()
    print("Guardado:", DST)


if __name__ == "__main__":
    main()
