# -*- coding: utf-8 -*-
"""Reconstruye el PDF: texto a 14 pt, sin paginas en blanco de solo
'Figure N', y con la imagen de cada figure colocada en el punto del texto
que la menciona (o en su pie de figure).
"""
import os
import re
import sys

import fitz
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) or ".")
from insertar_figuras import build_fig_map

SRC = "the-wave-principle.pdf"
DST = "el principio de la onda - reorganizado.pdf"
FIG_DIR = "figures_extraidas"

W, H = 612.0, 792.0
ML, MR, MT, MB = 64.0, 64.0, 62.0, 62.0
BODY, BOLD = "times-roman", "times-bold"
FS, LEAD = 14.0, 17.2
FIG_MAXW, FIG_MAXH = 400.0, 330.0
# separacion vertical (pt) entre lineas que marca un corte de parrafo:
# el interlineado normal deja ~4-6 pt; una linea en blanco deja ~19-31
GAP_BREAK = 10.0


def clean(t):
    t = t.replace("\xa0", " ").replace("\x00", "").replace("\xad", "")
    t = t.replace("\u037e", ";").replace("\u25a0", " ")
    t = t.replace("\u2014", "-").replace("\u2013", "-")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u2026", "...").replace("\u2022", "·")
    t = re.sub(r"\s+", " ", t).strip()
    # fuera de la codificacion del PDF: quitar lo que quede ilegible
    return "".join(
        c if (32 <= ord(c) < 127 or 160 <= ord(c) < 256) else " "
        for c in t)


def looks_like_text(t):
    letters = sum(c.isalpha() for c in t)
    if letters == 0:
        return False
    if re.fullmatch(r"[IVXLivxl]+\.?", t):
        return True
    toks = [w for w in re.split(r"\s+", t) if w]
    # palabras reales: fragmentos cortos o signos (basura de OCR de
    # graficos) no cuentan
    strong = sum(1 for w in toks if re.search(r"[A-Za-zÀ-ÿ]{3,}", w))
    if strong >= 2 and letters >= 8 and strong / len(toks) >= 0.20:
        return True
    return False


def is_heading(t):
    if len(t) > 70:
        return False
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 2:
        return False
    return sum(c.isupper() for c in letters) / len(letters) > 0.85


def page_paras(page):
    """Une la capa de texto (1 linea visual = 1 bloque a veces) en parrafos.

    Corte por hueco vertical > GAP_BREAK; une palabras partidas por guion
    de fin de linea.
    """
    lines = []
    for b in page.get_text("dict")["blocks"]:
        if b["type"] != 0:
            continue
        for l in b["lines"]:
            raw = "".join(s["text"] for s in l["spans"])
            t = clean(raw)
            if not t:
                continue
            rstripped = raw.rstrip()
            if rstripped.endswith("\xad"):
                hyp = "soft"
            elif rstripped.endswith("-"):
                hyp = "minus"
            else:
                hyp = None
            lines.append((l["bbox"][1], l["bbox"][3], l["bbox"][0], t, hyp))
    lines.sort(key=lambda x: (x[0], x[2]))

    paras = []
    cur = []
    prev = None  # (y1, texto, hip)
    for y0, y1, _x0, t, hyp in lines:
        if prev is not None and cur and (y0 - prev[0]) > GAP_BREAK:
            paras.append(" ".join(cur))
            cur = []
        if cur and prev is not None:
            ptxt = prev[1]
            if hyp == "soft" and t[:1].isalpha():
                cur[-1] = ptxt + "-" + t
            elif (hyp == "minus" and t[:1].isalpha()
                    and re.search(r"[A-Za-zÀ-ÿ]-$", ptxt)):
                cur[-1] = ptxt[:-1] + t
            else:
                cur.append(t)
        else:
            cur.append(t)
        prev = (y1, t, hyp)
    if cur:
        paras.append(" ".join(cur))
    return paras


class Book:
    def __init__(self, doc):
        self.doc = doc
        self.page = None
        self.y = 0.0
        self.n = 0
        self._new_page()

    def _new_page(self):
        self.page = self.doc.new_page(width=W, height=H)
        self.y = MT
        self.n += 1

    def _foot(self):
        if self.n == 1:  # portada sin numerar
            return
        self.page.insert_textbox(
            fitz.Rect(ML, H - 48, W - MR, H - 30), str(self.n),
            fontname=BODY, fontsize=10, align=fitz.TEXT_ALIGN_CENTER)

    def space(self, h):
        if self.y + h > H - MB:
            self._foot()
            self._new_page()

    def para(self, text, size=FS, bold=False, align=fitz.TEXT_ALIGN_JUSTIFY,
             gap_after=8.0, indent=0.0):
        font = BOLD if bold else BODY
        if self.y >= H - MB - 24:
            self._foot()
            self._new_page()
        while True:
            rect = fitz.Rect(ML + indent, self.y, W - MR, H - MB - 4)
            rc = self.page.insert_textbox(
                rect, text, fontname=font, fontsize=size, align=align,
                lineheight=LEAD / size)
            if rc >= 0:
                self.y += (rect.height - rc) + gap_after
                return
            # no cabe: si es la primera linea de la pagina, probar pagina nueva
            if self.y > MT + 1:
                self._foot()
                self._new_page()
                continue
            # párrafo más alto que la página: partirlo
            words = text.split()
            half = len(words) // 2
            if half == 0:
                self._foot()
                self._new_page()
                continue
            self.para(" ".join(words[:half]), size, bold, align, 0.0, indent)
            self.para(" ".join(words[half:]), size, bold, align, gap_after,
                      indent)
            return

    def figure(self, n, png):
        iw, ih = Image.open(png).size
        sc = min(FIG_MAXW / iw, FIG_MAXH / ih)
        w, h = iw * sc, ih * sc
        self.space(h + 34)
        x0 = ML + (W - ML - MR - w) / 2.0
        self.page.insert_image(fitz.Rect(x0, self.y, x0 + w, self.y + h),
                               filename=png)
        self.y += h + 4
        self.para("Figure %d" % n, size=12, bold=True,
                  align=fitz.TEXT_ALIGN_CENTER, gap_after=12)

    def finish(self):
        if self.y > MT + 1:
            self._foot()


def main():
    src = fitz.open(SRC)
    figmap, _ = build_fig_map(src)
    plates = set(figmap.values())
    pngs = {n: os.path.join(FIG_DIR, "figure_%02d.png" % n)
            for n in figmap}
    pngs = {n: p for n, p in pngs.items() if os.path.exists(p)}

    out = fitz.open()
    book = Book(out)
    placed = set()

    # portada
    for img in src[0].get_images(full=True):
        for r in src[0].get_image_rects(img[0]):
            if r.width > 500:
                pix = src[0].get_pixmap(dpi=110)
                cov = Image.frombytes("RGB", (pix.width, pix.height),
                                      pix.samples)
                cov.save("_cover.png")
                sc = min((W - 2 * ML) / cov.width, (H - 2 * MT) / cov.height)
                w, h = cov.width * sc, cov.height * sc
                x0 = (W - w) / 2
                book.page.insert_image(
                    fitz.Rect(x0, MT, x0 + w, MT + h), filename="_cover.png")
                book.y = min(MT + h + 20, H - MB - 10)
                break
        break

    events = []  # ('text', str, es_titulo) | ('figure', n, png)
    pending = []

    def flush():
        nonlocal pending
        for n, p in pending:
            events.append(("figure", n, p))
            placed.add(n)
        pending = []

    def trigger(n):
        if n in pngs and n not in placed and n not in [q[0] for q in pending]:
            pending.append((n, pngs[n]))

    for pno in range(1, src.page_count):
        page = src[pno]
        es_plancha = pno in plates
        for para in page_paras(page):
            if es_plancha:
                # plancha: solo recuperar prosa real (p. ej. la pagina 13)
                nwords = len([w for w in para.split() if len(w) >= 3])
                if nwords < 12 or not re.search(r"figure", para, re.I):
                    continue
            m = re.fullmatch(r"Figure\s*(\d+)\.?", para)
            if m:
                trigger(int(m.group(1)))
                flush()
                continue
            if not looks_like_text(para):
                continue
            events.append(("text", para, is_heading(para)))
            for mm in re.finditer(r"Figures?\s+(\d+)", para):
                trigger(int(mm.group(1)))
            for mm in re.finditer(r"\bfigures?\s+(\d+)", para, re.I):
                trigger(int(mm.group(1)))
            flush()

        # respaldo: menciones de la página que no quedaron disparadas
        full = clean(page.get_text())
        for mm in re.finditer(r"Figures?\s+(\d+)", full, re.I):
            n = int(mm.group(1))
            if n in pngs and n not in placed and n not in [q[0] for q in pending]:
                pending.append((n, pngs[n]))
        flush()

    # fusionar fragmentos de texto que continuan entre paginas
    merged = []
    for ev in events:
        if ev[0] == "text" and merged and merged[-1][0] == "text":
            ptxt, phead = merged[-1][1], merged[-1][2]
            ntxt, nhead = ev[1], ev[2]
            if (not phead and not nhead
                    and not re.search(r"[.!?…\"]\s*$", ptxt)
                    and not re.match(r"[¹²³]|Figure\s*\d", ntxt)):
                merged[-1] = ("text", ptxt + " " + ntxt, False)
                continue
        merged.append(ev)

    for ev in merged:
        if ev[0] == "figure":
            book.figure(ev[1], ev[2])
        elif ev[2]:
            book.para(ev[1], bold=True, align=fitz.TEXT_ALIGN_CENTER,
                      gap_after=14)
        else:
            book.para(ev[1])

    book.finish()
    out.save(DST, garbage=3, deflate=True)
    print("Paginas nuevas:", out.page_count)
    print("Figures colocadas:", len(placed), "de", len(pngs))
    faltan = sorted(set(pngs) - placed)
    if faltan:
        print("Sin colocar:", faltan)
    out.close()
    src.close()
    if os.path.exists("_cover.png"):
        os.remove("_cover.png")
    print("Guardado:", DST)


if __name__ == "__main__":
    main()
