# -*- coding: utf-8 -*-
"""Quita los fondos/marcas de color (subrayados azules, marcas amarillas y cian)
de las paginas escaneadas, dejando el texto en negro sobre blanco.

Solo toca las paginas que tienen color de marcado; la portada, las figures
y el resto de paginas quedan intactas.
"""
import io
import sys

import numpy as np
import fitz
from PIL import Image

SRC = "el principio de la onda - con figures en linea.pdf"
DST = "el principio de la onda - final.pdf"

# paginas (0-based) con subrayados/marcas de color en el escaneo
PAGINAS = [2, 7, 8, 9, 10, 11, 33, 34, 47, 91, 121, 122, 138,
           142, 155, 162, 170, 171, 196]


def procesar(im):
    """Elimina el color de marcado; el texto oscuro pasa a gris/negro."""
    a = np.asarray(im).astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = mx - mn
    lum = a.mean(axis=2)

    colored = sat > 25
    # fondo/subrayado/marca: los canales no-azules tambien son claros
    claro = colored & (np.minimum(r, g) > 130)
    # texto con tinte de color: lo demas
    tintado = colored & ~claro

    out = a.copy()
    out[claro] = 255
    gris = np.clip(lum[tintado], 0, 255)
    out[tintado] = gris[:, None]
    return Image.fromarray(out.astype(np.uint8))


def limpiar_pagina(doc, i):
    page = doc[i]
    # localizar el escaneo que cubre casi toda la pagina
    objetivo = None
    for img in page.get_images(full=True):
        xref = img[0]
        for r in page.get_image_rects(xref):
            if r.width >= 600 and r.height >= 780:
                objetivo = xref
    if objetivo is None:
        print("pagina", i, ": sin escaneo a pagina completa, omitida")
        return
    info = doc.extract_image(objetivo)
    im = Image.open(io.BytesIO(info["image"])).convert("RGB")
    im2 = procesar(im)
    buf = io.BytesIO()
    im2.save(buf, format="JPEG", quality=90, subsampling=0)
    page.replace_image(objetivo, stream=buf.getvalue())
    print("pagina", i, ": color de texto eliminado")


def main():
    doc = fitz.open(SRC)
    paginas = [int(x) for x in sys.argv[1:]] or PAGINAS
    for i in paginas:
        limpiar_pagina(doc, i)
    doc.save(DST, garbage=3, deflate=True)
    doc.close()
    print("Guardado:", DST)


if __name__ == "__main__":
    main()
