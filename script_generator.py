#!/usr/bin/env python3
"""Genera ofertas por tabs de acuerdo a dominios configurados y new_stores.txt.

Por cada tab/dominio:
1. Revisa el último upload. Si no está dentro de las últimas 48 horas, se descarta.
2. Trae los productos destacados ordenados por mayor descuento (variacion_promedio).
3. Normaliza enlaces, precios, títulos y descarga miniaturas WebP.
4. Genera tabs.json, libros.json y los JSONs individuales de cada tab.
5. Genera index.html con navegación interactiva por tabs y páginas HTML dedicadas
   (paris.html, cic.html, etc.) con Schema.org JSON-LD inyectado para SEO.

Uso:
    export ONESOURCE_TOKEN="tu_token"   # o ponerlo en .env
    python3 script_generator.py
"""
import hashlib
import html
import io
import json
import os
import re
import shutil
import sys
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    from PIL import Image
except ImportError:
    Image = None

DIR_BASE = os.path.dirname(os.path.abspath(__file__))

# Cargar .env si existe
ENV_PATH = os.path.join(DIR_BASE, ".env")
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

BASE = "http://onesource.cl/api"
TOKEN = os.environ.get("ONESOURCE_TOKEN")
# Solo el origen (https://dominio): los links, el canonical y el sitemap usan rutas desde la raíz (/abc)
_u = urllib.parse.urlsplit(os.environ.get("SITE_URL", "https://fernandoandresmunoz.github.io"))
SITE_URL = f"{_u.scheme}://{_u.netloc}" if _u.netloc else "https://fernandoandresmunoz.github.io"
CARPETA_IMG = "imagenes"
RUTA_IMG = os.path.join(DIR_BASE, CARPETA_IMG)
ANCHO_IMG, ALTO_IMG = 96, 128
LD_MAX = 100
MAX_HORAS_UPLOAD = 48  # Regla de new_stores.txt: si el upload no está dentro de 48h, descartar

# Historial de precios (sparkline): /registers/?article=<id>. Si onesource.cl no lo expone,
# define REGISTERS_URL en .env (p. ej. http://192.168.1.22/api; solo alcanzable desde esa red).
REGISTERS_BASE = os.environ.get("REGISTERS_URL", BASE).rstrip("/")
HIST_PAGE_SIZE = 250   # registros que se piden por producto
HIST_PUNTOS = 40       # puntos que se guardan en el JSON (muestreo parejo, incluye primero y último)
DET_REGISTROS = 8      # filas de "Últimos registros" en la página de detalle

TABS_CONFIG = [
    {
        "id": "libros",
        "name": "Libros",
        "icon": "📚",
        "headline": "Los mejores descuentos de libros en Chile, <em>actualizados hoy.</em>",
        "subtitle": "Comparamos precios y descuentos reales entre las principales librerías del país diariamente. Encuentra tu próxima lectura al mejor precio.",
        "item_label": "Libro",
        "domains": [4, 1, 16],
        "top": 250,
        "por_dominio": 250,
        "max_descuento": 95.0,
        "json_file": "libros.json",
        "html_file": "index.html",
    },
    {
        "id": "paris",
        "name": "Paris",
        "icon": "🏬",
        "headline": "Los mejores descuentos en Paris.cl, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas destacadas con mayor variación y descuento en Paris.",
        "item_label": "Producto",
        "domains": [42],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "paris.json",
        "html_file": "paris.html",
    },
    {
        "id": "cic",
        "name": "CIC",
        "icon": "🛏️",
        "headline": "Los mejores descuentos en CIC, <em>actualizados hoy.</em>",
        "subtitle": "Camas, colchones, muebles y dormitorios con los mejores precios.",
        "item_label": "Producto",
        "domains": [17],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "cic.json",
        "html_file": "cic.html",
    },
    {
        "id": "falabella",
        "name": "Falabella Tecno",
        "icon": "💻",
        "headline": "Los mejores descuentos en Falabella Tecnología, <em>actualizados hoy.</em>",
        "subtitle": "Notebooks, smartphones, audio y tecnología con mayores descuentos reales.",
        "item_label": "Producto",
        "domains": [50],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "falabella.json",
        "html_file": "falabella.html",
    },
    {
        "id": "wei",
        "name": "Wei.cl",
        "icon": "🖥️",
        "headline": "Los mejores descuentos en Wei.cl, <em>actualizados hoy.</em>",
        "subtitle": "Computación, hardware, periféricos y accesorios gamer al mejor precio.",
        "item_label": "Producto",
        "domains": [32],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "wei.json",
        "html_file": "wei.html",
    },
    {
        "id": "labarra",
        "name": "La Barra",
        "icon": "🍻",
        "headline": "Los mejores descuentos en La Barra, <em>actualizados hoy.</em>",
        "subtitle": "Cervezas, vinos, licores y bebidas con ofertas imperdibles.",
        "item_label": "Producto",
        "domains": [60],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "labarra.json",
        "html_file": "labarra.html",
    },
    {
        "id": "jumbo",
        "name": "Jumbo",
        "icon": "🛒",
        "headline": "Los mejores descuentos en Jumbo, <em>actualizados hoy.</em>",
        "subtitle": "Supermercado, despensa, vinos, carnes y frescos con ofertas reales.",
        "item_label": "Producto",
        "domains": [79],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "jumbo.json",
        "html_file": "jumbo.html",
    },
    {
        "id": "unimarc",
        "name": "Unimarc",
        "icon": "🛍️",
        "headline": "Los mejores descuentos en Unimarc, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas del día y ahorro en supermercado Unimarc.",
        "item_label": "Producto",
        "domains": [66],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "unimarc.json",
        "html_file": "unimarc.html",
    },
    {
        "id": "abc",
        "name": "abc",
        "icon": "📺",
        "headline": "Los mejores descuentos en abc, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas destacadas con mayor variación y descuento en abc.",
        "item_label": "Producto",
        "domains": [6],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "abc.json",
        "html_file": "abc.html",
    },
    {
        "id": "automoviles",
        "name": "Automóviles",
        "icon": "🚗",
        "headline": "Los mejores descuentos de autos en Chile, <em>actualizados hoy.</em>",
        "subtitle": "Vehículos, autos usados y nuevos con mayor baja de precio.",
        "item_label": "Vehículo",
        "domains": [5],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "automoviles.json",
        "html_file": "automoviles.html",
    },
    {
        "id": "8bits",
        "name": "8-Bits",
        "icon": "🕹️",
        "headline": "Los mejores descuentos en 8-Bits, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas destacadas con mayor variación y descuento en 8-Bits.",
        "item_label": "Producto",
        "domains": [33],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "8bits.json",
        "html_file": "8bits.html",
    },
    {
        "id": "bazar",
        "name": "Bazar Historias",
        "icon": "🎁",
        "headline": "Los mejores descuentos en Bazar Historias, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas destacadas con mayor variación y descuento en Bazar Historias.",
        "item_label": "Producto",
        "domains": [11],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "bazar.json",
        "html_file": "bazar.html",
    },
    {
        "id": "limonada",
        "name": "Limonada",
        "icon": "🍋",
        "headline": "Los mejores descuentos en Limonada, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas destacadas con mayor variación y descuento en Limonada.",
        "item_label": "Producto",
        "domains": [31],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "limonada.json",
        "html_file": "limonada.html",
    },
    {
        "id": "megagames",
        "name": "Megagames",
        "icon": "🎮",
        "headline": "Los mejores descuentos en Megagames, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas destacadas con mayor variación y descuento en Megagames.",
        "item_label": "Producto",
        "domains": [34],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "megagames.json",
        "html_file": "megagames.html",
    },
    {
        "id": "schilling",
        "name": "Ópticas Schilling",
        "icon": "👓",
        "headline": "Los mejores descuentos en Ópticas Schilling, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas destacadas con mayor variación y descuento en Ópticas Schilling.",
        "item_label": "Producto",
        "domains": [9],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "schilling.json",
        "html_file": "schilling.html",
    },
    {
        "id": "solotodo",
        "name": "Solotodo",
        "icon": "🔌",
        "headline": "Los mejores descuentos en Solotodo, <em>actualizados hoy.</em>",
        "subtitle": "Ofertas destacadas con mayor variación y descuento en Solotodo.",
        "item_label": "Producto",
        "domains": [120],
        "top": 100,
        "por_dominio": 100,
        "max_descuento": 90.0,
        "json_file": "solotodo.json",
        "html_file": "solotodo.html",
    },
]

NOMBRES_TIENDA = {
    "buscalibre.cl": "Buscalibre",
    "antartica.cl": "Antártica",
    "feriachilenadellibro.cl": "Feria Chilena del Libro",
    "paris.cl": "Paris",
    "cic.cl": "CIC",
    "falabella-tecnologia.com": "Falabella",
    "wei.cl": "Wei.cl",
    "labarra.cl": "La Barra",
    "jumbo.cl": "Jumbo",
    "unimarc.cl": "Unimarc",
    "automoviles.emol.com": "Emol Automóviles",
    "www.lapolar.cl": "abc",
}


def get(path, **params):
    url = path if path.startswith("http") else f"{BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Token {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def nombres_dominios():
    try:
        doms = {}
        resp = get("/domains/", page_size=100)
        for d in resp.get("results", []):
            doms[d["id"]] = d["name"]
        if resp.get("next"):
            resp2 = get(resp["next"])
            for d in resp2.get("results", []):
                doms[d["id"]] = d["name"]
        return doms
    except Exception as e:
        print(f"  (no pude leer /domains/: {e})")
        return {}


def ultimo_upload(dominio):
    """Devuelve el último upload terminado si fue creado dentro de MAX_HORAS_UPLOAD, si no None.

    Si el más reciente aún está en ejecución (finished_at vacío, sin datos 'featured'),
    se usa el anterior que sí terminó. Si ninguno de los últimos tiene finished_at,
    se mantiene el comportamiento anterior (el más reciente).
    """
    try:
        res = get("/uploads/", domain=dominio, page_size=5).get("results", [])
        if not res:
            return None
        up = next((u for u in res if u.get("finished_at")), res[0])
        created_str = up.get("created_at")
        if created_str:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            diff_hours = (datetime.now(timezone.utc) - created).total_seconds() / 3600
            up["horas_antiguedad"] = diff_hours
            if diff_hours > MAX_HORAS_UPLOAD:
                print(f"  ⚠ Upload {up['id']} (dominio {dominio}) tiene {diff_hours:.1f}h (> {MAX_HORAS_UPLOAD}h). Descartado.")
                return None
        return up
    except Exception as e:
        print(f"  ✗ error al consultar upload dominio {dominio}: {e}")
        return None


def mejores_del_dominio(upload_id, por_dominio=100):
    """Top items ordenados por variacion_promedio (más negativa = mayor descuento)."""
    items = []
    page_size = min(por_dominio, 250)
    resp = get("/featured/", upload__id=upload_id, page_size=page_size,
               ordering="variacion_promedio", variacion_precio_minimo=0)
    while True:
        items += resp.get("results", [])
        if len(items) >= por_dominio or not resp.get("next"):
            break
        try:
            resp = get(resp["next"])
        except Exception:
            break
    return items[:por_dominio]


def arreglar_url(url, tienda):
    if not url:
        return ""
    url = url.strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(("http://", "https://")):
        return url
    t = (tienda or "").lower()
    if "buscalibre" in t:
        return urllib.parse.urljoin("https://www.buscalibre.cl/", url)
    if "paris" in t:
        return urllib.parse.urljoin("https://www.paris.cl/", url)
    if "cic" in t:
        if url.startswith("cic.cl"):
            return "https://" + url
        return urllib.parse.urljoin("https://www.cic.cl/", url)
    if "falabella" in t:
        return urllib.parse.urljoin("https://www.falabella.com/", url)
    if "wei" in t:
        return urllib.parse.urljoin("https://www.wei.cl/", url)
    if "labarra" in t:
        return urllib.parse.urljoin("https://labarra.cl/", url)
    if "jumbo" in t:
        return urllib.parse.urljoin("https://www.jumbo.cl/", url)
    if "unimarc" in t:
        return urllib.parse.urljoin("https://www.unimarc.cl/", url)
    if "lapolar" in t or "abc" in t:
        return urllib.parse.urljoin("https://www.lapolar.cl/", url)
    if "emol" in t or "automoviles" in t:
        return urllib.parse.urljoin("http://automoviles.emol.com/", url)
    # Tienda sin regla propia: si el link es una ruta (/producto/...), se completa con el dominio de la tienda
    if url.startswith("/") and "." in t:
        return urllib.parse.urljoin("https://" + t + "/", url)
    return "https://" + url


_MAYUS = {"\x81": "Á", "\x89": "É", "\u2030": "É", "\x8d": "Í", "\x93": "Ó", "\u201c": "Ó", '"': "Ó",
          "\x9a": "Ú", "\u0161": "Ú", "\x91": "Ñ", "\u2018": "Ñ", "'": "Ñ", "\x9c": "Ü", "\u0153": "Ü"}
_RE_MAYUS = re.compile("([Ãã])([" + re.escape("".join(_MAYUS)) + "])")
_RE_MINUS = re.compile("[Ãã]([\xa0-\xbf])")
_RE_SIGNOS = re.compile("Â([\xa0-\xbf])")


def limpiar_texto(s):
    if not isinstance(s, str):
        return s
    s = " ".join(s.split())
    fix = _RE_MAYUS.sub(lambda m: _MAYUS[m.group(2)] if m.group(1) == "Ã" else _MAYUS[m.group(2)].lower(), s)
    fix = _RE_MINUS.sub(lambda m: bytes([0xC3, ord(m.group(1))]).decode("utf-8"), fix)
    fix = _RE_SIGNOS.sub(lambda m: m.group(1), fix)
    if fix != s:
        fix = re.sub(r"([áéíóúñü])([A-ZÁÉÍÓÚÑ])(?![A-Za-záéíóúñ])",
                     lambda m: m.group(1) + m.group(2).lower(), fix)
    return fix


def normalizar(it, dominio_id, tienda):
    desc = round(-(it.get("variacion_promedio") or 0), 1)
    return {
        "id": it.get("id"),
        "title": limpiar_texto(it.get("title")),
        "dominio_id": dominio_id,
        "tienda": tienda,
        "precio": it.get("precio"),
        "promedio": it.get("promedio"),
        "descuento": desc,
        "variacion_ultimo_precio": it.get("variacion_ultimo_precio"),
        "thumbnail": it.get("thumbnail"),
        "author": limpiar_texto(it.get("author")),
        "url": arreglar_url(it.get("link") or it.get("url"), tienda),
    }


EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
       "image/webp": "webp", "image/gif": "gif", "image/avif": "avif"}


def descargar_imagen(url, tienda):
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    url = arreglar_url(url, tienda)
    if not url.startswith(("http://", "https://")):
        return None
    nombre = hashlib.sha1(url.encode()).hexdigest()[:12]
    for f in os.listdir(RUTA_IMG):
        if f.startswith(nombre + ".") and (Image is None or f.endswith(".webp")):
            return f"{CARPETA_IMG}/{f}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            ext = EXT.get(r.headers.get_content_type())
            datos = r.read(5_000_000)
        if not ext:
            return None
        if Image is not None:
            im = Image.open(io.BytesIO(datos))
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            im.thumbnail((ANCHO_IMG, ALTO_IMG), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, "WEBP", quality=75, method=6)
            datos, ext = buf.getvalue(), "webp"
        archivo = f"{nombre}.{ext}"
        with open(os.path.join(RUTA_IMG, archivo), "wb") as f:
            f.write(datos)
        return f"{CARPETA_IMG}/{archivo}"
    except Exception as e:
        return None


def guardar_imagenes(todos_los_items):
    os.makedirs(RUTA_IMG, exist_ok=True)
    with ThreadPoolExecutor(12) as ex:
        rutas = list(ex.map(lambda b: descargar_imagen(b.get("thumbnail"), b.get("tienda")), todos_los_items))
    for b, ruta in zip(todos_los_items, rutas):
        if ruta:
            b["thumbnail"] = ruta
    usadas = {os.path.basename(r) for r in rutas if r}
    total_imgs = len([f for f in os.listdir(RUTA_IMG) if os.path.isfile(os.path.join(RUTA_IMG, f))])
    print(f"✓ {len(usadas)} imágenes activas descargadas/verificadas. Total conservadas en {RUTA_IMG}: {total_imgs}")


def slugify(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text.lower())
    text = re.sub(r"[-\s]+", "-", text).strip("-")
    return text[:70].rstrip("-")


def _a_epoch(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return int(d.timestamp())
    except Exception:
        return None


def historial_precios(article_id):
    """Pide los registros del artículo una sola vez y devuelve (hist, detalle).

    hist    -> hasta HIST_PUNTOS valores para el mini gráfico de la tabla.
    detalle -> datos de la página de detalle: n (registros), s (puntos donde cambia el valor, como
               [epoch, valor], más el último) y r (últimos registros, del más nuevo al más antiguo).
    Devuelve (None, None) si hay menos de 2 registros.
    """
    resp = get(f"{REGISTERS_BASE}/registers/", article=article_id, ordering="-obtained", page_size=HIST_PAGE_SIZE)
    regs = [(_a_epoch(r.get("obtained")), round(r["precio"])) for r in reversed(resp.get("results", [])) if r.get("precio")]
    if len(regs) < 2:
        return None, None
    hist = [v for _, v in regs]
    if len(hist) > HIST_PUNTOS:
        n = len(hist) - 1
        hist = [hist[round(i * n / (HIST_PUNTOS - 1))] for i in range(HIST_PUNTOS)]
    detalle = None
    con_fecha = [(t, v) for t, v in regs if t is not None]
    if len(con_fecha) >= 2:
        s = [list(con_fecha[0])]
        for t, v in con_fecha[1:-1]:
            if v != s[-1][1]:
                s.append([t, v])
        s.append(list(con_fecha[-1]))
        detalle = {"n": len(regs), "s": s, "r": [list(x) for x in reversed(con_fecha[-(DET_REGISTROS + 1):])]}
    return hist, detalle


def guardar_historiales(items):
    """Agrega 'hist' (lista de precios), '_det' y 'slug' a cada item. Deduplica por article_id."""
    estado = {"ok": 0, "fallos": 0}
    unique_items = {}
    for b in items:
        bid = b.get("id")
        if bid and bid not in unique_items:
            unique_items[bid] = b

    def fetch_uno(b):
        if estado["ok"] == 0 and estado["fallos"] >= 20:  # el endpoint no responde: no insistir
            return b["id"], (None, None)
        try:
            r = historial_precios(b["id"])
            estado["ok"] += 1
            return b["id"], r
        except Exception:
            estado["fallos"] += 1
            return b["id"], (None, None)

    with ThreadPoolExecutor(8) as ex:
        resultados = dict(ex.map(fetch_uno, list(unique_items.values())))

    for b in items:
        h, d = resultados.get(b.get("id"), (None, None))
        b["hist"] = h
        if d:
            b["_det"] = d   # datos de la página de detalle (no va al JSON de la lista)
            b["slug"] = f"{b['id']}-{slugify(b.get('title', ''))}"
            b["det"] = 1    # marca de soporte
    con = sum(1 for b in items if b.get("hist"))
    print(f"✓ Historial de precios: {con}/{len(items)} productos ({estado['fallos']} errores)")
    if estado["ok"] == 0 and estado["fallos"]:
        print(f"  ⚠ /registers/ no respondió en {REGISTERS_BASE}; define REGISTERS_URL en .env")


def generar_json_ld(items, tab_info):
    schema_items = []
    is_book = tab_info["id"] == "libros"
    for i, b in enumerate(items[:LD_MAX], 1):
        oferta = {
            "@type": "Offer",
            "price": str(round(b["precio"])),
            "priceCurrency": "CLP",
            "availability": "https://schema.org/InStock",
            "seller": {"@type": "Organization",
                       "name": NOMBRES_TIENDA.get(b["tienda"], b["tienda"])},
        }
        if b.get("url"):
            oferta["url"] = b["url"]
        item_types = ["Product", "Book"] if is_book else "Product"
        prod = {"@type": item_types, "name": b["title"], "offers": oferta}
        if is_book and b.get("author"):
            prod["author"] = {"@type": "Person", "name": b["author"]}
        if SITE_URL and b.get("thumbnail"):
            prod["image"] = f"{SITE_URL}/{b['thumbnail']}"
        schema_items.append({"@type": "ListItem", "position": i, "item": prod})
    return {
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": tab_info["name"] + " - Ofertas y Descuentos en Chile",
        "numberOfItems": len(schema_items),
        "itemListElement": schema_items,
    }


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="/">
<title>__PAGE_TITLE__</title>
<meta name="description" content="__PAGE_DESC__">
__CANONICAL_TAG__
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag.js?id=G-CJRB34M9L8"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());
  gtag('config', 'G-CJRB34M9L8');
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,800;1,9..144,500&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0b0f17;--card:#121a28;--card2:#172235;--hov:#1a253a;--ink:#e9eef7;--mute:#8d99ae;--line:#223048;--acc:#ff6b3d;--acc2:#ffc857;--pr:#52e19b;--onacc:#fff;--up:#f87171;--dn:#4ade80;--fl:#94a3b8}
@media (prefers-color-scheme:light){:root{--bg:#f3f5f9;--card:#fff;--card2:#f8fafc;--hov:#e9edf5;--ink:#0b0f17;--mute:#5a6578;--line:#dbe1ec;--acc:#ec5a2b;--acc2:#b7791f;--pr:#1f9d63;--onacc:#fff;--up:#dc2626;--dn:#16a34a;--fl:#64748b}}
*{box-sizing:border-box;margin:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--ink);font:400 14px/1.45 Inter,system-ui,sans-serif;min-height:100vh;background-image:radial-gradient(900px 480px at 90% -10%,rgba(255,107,61,.09),transparent),radial-gradient(700px 380px at -5% 5%,rgba(82,225,155,.08),transparent)}
.wrap{max-width:1260px;margin:auto;padding:0 20px}
.hero{padding:44px 0 20px}
.brand{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;font:800 20px Fraunces,Georgia,serif}
.brand b{color:var(--acc)}.brand em{font:500 14px Inter,sans-serif;color:var(--mute);font-style:normal}
.brand small{font:500 12px Inter;color:var(--mute);border:1px solid var(--line);padding:5px 12px;border-radius:99px;background:var(--card)}
h1{font:800 clamp(32px,5.5vw,64px)/1.05 Fraunces,Georgia,serif;letter-spacing:-.035em;margin:32px 0 14px;max-width:880px}
h1 em{font-style:italic;font-weight:500;background:linear-gradient(100deg,var(--acc),var(--acc2));-webkit-background-clip:text;background-clip:text;color:transparent}
.lead{color:var(--mute);font-size:16px;line-height:1.55;max-width:680px}
.stats{display:flex;gap:28px;margin-top:22px;flex-wrap:wrap}
.stats div{font:800 28px Fraunces,serif}
.stats span{display:block;font:500 11px Inter;color:var(--mute);text-transform:uppercase;letter-spacing:.1em}

/* Tabs Bar */
.tabs-wrap{margin:28px auto 16px;display:flex;justify-content:center;width:100%}
.tabs-scroll{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:8px 10px;margin:0 auto;max-width:100%}
@media (max-width:768px){.tabs-scroll{flex-wrap:nowrap;justify-content:flex-start;overflow-x:auto;padding-bottom:10px;scrollbar-width:thin}}
.tabs-scroll::-webkit-scrollbar{height:5px}
.tabs-scroll::-webkit-scrollbar-thumb{background:var(--line);border-radius:99px}
.tab-btn{flex:none;display:inline-flex;align-items:center;gap:8px;padding:8px 15px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:600 13px Inter,sans-serif;cursor:pointer;transition:all .2s ease;text-decoration:none;user-select:none}
.tab-btn:hover{border-color:var(--acc);transform:translateY(-1px);background:var(--hov)}
.tab-btn.active{background:linear-gradient(135deg,rgba(255,107,61,.18),rgba(255,61,113,.15));border-color:var(--acc);color:var(--ink);box-shadow:0 0 18px -4px rgba(255,107,61,.4)}
.tab-btn .t-icon{font-size:15px;line-height:1}
.tab-btn .t-badge{font-size:11px;font-weight:700;padding:2px 7px;border-radius:99px;background:color-mix(in srgb,var(--mute) 24%,transparent);color:var(--mute);transition:all .2s}
.tab-btn.active .t-badge{background:var(--acc);color:#fff}

/* Sticky Filter Bar */
.bar{position:sticky;top:0;z-index:10;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:blur(14px);border-block:1px solid var(--line);padding:12px 0}
.row{display:flex;gap:10px;flex-wrap:wrap}
input,select{background:var(--card);border:1px solid var(--line);color:var(--ink);padding:11px 15px;border-radius:12px;font:inherit;outline:none;transition:border-color .15s,box-shadow .15s}
input{flex:1 1 240px}input:focus,select:focus{border-color:var(--acc);box-shadow:0 0 0 4px rgba(255,107,61,.18)}
.chips{margin-top:10px;display:flex;gap:8px;overflow-x:auto;scrollbar-width:none}
.chip{flex:none;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--ink);padding:6px 14px;border-radius:99px;font:500 13px Inter;display:flex;gap:7px;align-items:center;transition:all .15s}
.chip i{width:8px;height:8px;border-radius:50%;background:var(--c)}.chip:hover{border-color:var(--acc)}.chip.on{background:var(--ink);color:var(--bg);border-color:var(--ink);font-weight:600}
.count{margin:22px 0 12px;color:var(--mute)}.count b{color:var(--ink)}

/* Table */
.tw{overflow-x:auto;border:1px solid var(--line);border-radius:20px;background:var(--card);box-shadow:0 12px 30px -10px rgba(0,0,0,.35)}
table{width:100%;border-collapse:collapse;min-width:860px}
th{text-align:left;font:700 11px Inter;letter-spacing:.1em;text-transform:uppercase;color:var(--mute);padding:15px 16px;border-bottom:1px solid var(--line);white-space:nowrap;user-select:none}
th[data-k]{cursor:pointer}th[data-k]:hover,th.s{color:var(--acc)}
.r{text-align:right}
td{padding:12px 16px;border-bottom:1px solid var(--line);vertical-align:middle}
tbody tr{transition:background .15s}tbody tr:hover{background:var(--hov)}tbody tr:last-child td{border-bottom:0}
.n{color:var(--mute);width:44px}
.bk{display:flex;gap:14px;align-items:center}
a.bk{color:inherit;text-decoration:none}a.bk:hover .t{color:var(--acc)}
.mc{flex:none;width:44px;height:56px;border-radius:6px;overflow:hidden;background:var(--line);box-shadow:2px 4px 10px -2px rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;color:var(--mute);font-size:18px}
.mc img{width:100%;height:100%;object-fit:cover;display:block}
.t{font:500 15px/1.25 Fraunces,serif;max-width:440px}
.st{display:inline-flex;gap:7px;align-items:center;padding:5px 11px;border-radius:99px;background:color-mix(in srgb,var(--c) 16%,transparent);font:500 12px Inter;white-space:nowrap}
.st i{width:7px;height:7px;border-radius:50%;background:var(--c)}
.o{color:var(--mute);text-decoration:line-through;white-space:nowrap;font-size:13px}
.p{font:800 17px Fraunces,serif;color:var(--pr);white-space:nowrap}
.d{display:flex;align-items:center;gap:9px;min-width:130px}
.d b{font:800 14px Fraunces,serif;width:48px;color:var(--acc2)}.d.hot b{color:var(--acc)}
.d span{flex:1;height:8px;border-radius:9px;background:color-mix(in srgb,var(--mute) 26%,transparent);overflow:hidden}
.d span i{display:block;height:100%;border-radius:9px;background:linear-gradient(90deg,var(--acc2),var(--acc),#ff3d71)}
.sv{color:var(--acc2);font-weight:600;white-space:nowrap}
.sp{display:flex;align-items:center;justify-content:flex-end;gap:10px}.sp svg{width:120px;height:34px;flex:none}
table.single .c-store{display:none}
.btn{display:inline-block;text-decoration:none;border:1px solid var(--line);color:var(--ink);padding:8px 14px;border-radius:10px;font:700 12px Inter;white-space:nowrap;transition:all .15s}
tr:hover .btn,.btn:hover{background:var(--acc);border-color:var(--acc);color:var(--onacc)}
.msg{text-align:center;padding:70px 20px;color:var(--mute);font:500 20px Fraunces,serif}.msg code{font:13px monospace;background:var(--hov);padding:3px 8px;border-radius:6px}
.pager{display:flex;justify-content:center;gap:6px;flex-wrap:wrap;margin:36px 0 16px}
.pager button{min-width:40px;height:40px;padding:0 12px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:500 14px Inter;cursor:pointer;transition:all .15s}
.pager button:hover:not(:disabled){border-color:var(--acc)}.pager button.on{background:linear-gradient(135deg,var(--acc),#ff3d71);border-color:transparent;color:var(--onacc);font-weight:700}
.pager button:disabled{opacity:.35;cursor:default}.pager span{align-self:center;color:var(--mute)}
footer{border-top:1px solid var(--line);margin-top:48px;padding:28px 0 48px;color:var(--mute);font-size:13px;text-align:center}
</style>
<script type="application/ld+json" id="ld-books">__JSON_LD_CONTENT__</script>
</head>
<body>
<div class="wrap">
<header class="hero">
  <div class="brand"><span>Páginas <b>&amp;</b> Precios <em>| Monitor de Ofertas</em></span><small id="upd">cargando…</small></div>
  <h1 id="hero-title">__HERO_TITLE__</h1>
  <p class="lead" id="hero-desc">__HERO_SUBTITLE__</p>
  <div class="stats">
    <div id="s1">–<span>Ofertas</span></div>
    <div id="s2">–<span>Tiendas</span></div>
    <div id="s3">–<span>Descuento promedio</span></div>
  </div>
</header>
</div>

<nav class="tabs-wrap wrap" aria-label="Categorías y Tiendas">
  <div class="tabs-scroll" id="tabs-nav"></div>
</nav>

<div class="bar"><div class="wrap">
  <div class="row">
    <input id="q" type="search" placeholder="Buscar..." aria-label="Buscar">
    <select id="min" aria-label="Descuento mínimo"><option value="0">Cualquier descuento</option><option value="20">+20%</option><option value="40">+40%</option><option value="50">+50%</option><option value="70">+70%</option></select>
    <select id="per" aria-label="Filas por página"><option selected>10</option><option>25</option><option>50</option><option value="1000">Todos</option></select>
  </div>
  <div class="chips" id="chips"></div>
</div></div>

<main class="wrap">
  <p class="count" id="count"></p>
  <div class="tw"><table>
    <thead><tr><th>#</th><th data-k="title" id="col-item">__ITEM_LABEL__</th><th data-k="tienda" class="c-store">Tienda</th><th class="r" data-k="precio">Precio</th><th data-k="descuento">Descuento</th><th class="r" data-k="ahorro">Ahorras</th><th class="r">Historial</th><th></th></tr></thead>
    <tbody id="body"></tbody>
  </table></div>
  <nav class="pager" id="pager"></nav>
</main>

<footer class="wrap">© Páginas &amp; Precios · Monitoreo automático de ofertas en Chile. Los precios y disponibilidad son responsabilidad de cada tienda.</footer>

<script>
window.PAGE_DEFAULT_TAB = "__DEFAULT_TAB_ID__";
const $ = id => document.getElementById(id),
  clp = n => "$" + Math.round(n).toLocaleString("es-CL"),
  esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const COLORS = ["#ff8a3d", "#4da3ff", "#6fe3a5", "#ff5d8f", "#b78cff", "#ffc857", "#20c997", "#fd7e14"];
let TABS = [], currentTab = null, B = [], D = [], page = 1, store = -1, key = "descuento", dir = -1, maxD = 1;
const tabCache = {};
const tabPath = t => t.path || (t.html_file && t.html_file !== "index.html" ? "/" + t.html_file.replace(".html", "") : "/");
const tabFromLocation = () => {
  let p = location.pathname.replace("/index.html", "/").replace(".html", "");
  if (p.length > 1 && p.endsWith("/")) p = p.slice(0, -1);
  return TABS.find(t => tabPath(t) === p);
};

fetch("tabs.json", { cache: "no-cache" })
  .then(r => { if (!r.ok) throw 0; return r.json(); })
  .then(data => {
    TABS = data.tabs || [];
    renderTabsBar();
    // Links viejos tipo /#abc: se llevan a la URL limpia /abc
    const legacy = TABS.find(t => t.id === (location.hash || "").replace("#", "").trim());
    const found = legacy || tabFromLocation();
    const initialId = found ? found.id : (TABS.find(t => t.id === window.PAGE_DEFAULT_TAB) || TABS[0])?.id;
    if (initialId) switchTab(initialId, false);
    if (legacy) history.replaceState(null, "", tabPath(legacy));
  })
  .catch(() => {
    // Fallback: intentar cargar libros.json directamente
    fetch("libros.json", { cache: "no-cache" }).then(r => r.json()).then(j => {
      initTabData({ id: "libros", name: "Libros", item_label: "Libro", headline: "Los mejores descuentos de libros en Chile", subtitle: "Librerías principales de Chile" }, j);
    }).catch(() => {
      $("upd").textContent = "sin datos";
      $("body").innerHTML = `<tr><td colspan="8"><div class="msg">No pude leer los datos.<br><br>Genera las ofertas con <code>python3 script_generator.py</code>.</div></td></tr>`;
    });
  });

function renderTabsBar() {
  const nav = $("tabs-nav");
  nav.innerHTML = TABS.map(t => `
    <a class="tab-btn${currentTab && currentTab.id === t.id ? " active" : ""}" href="${tabPath(t)}" data-tab="${t.id}" id="tab-btn-${t.id}">
      <span class="t-icon">${t.icon || "🏷️"}</span>
      <span>${esc(t.name)}</span>
      <span class="t-badge">${t.total || 0}</span>
    </a>
  `).join("");
}

$("tabs-nav").onclick = e => {
  const btn = e.target.closest(".tab-btn");
  if (!btn || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
  e.preventDefault();
  const tid = btn.dataset.tab;
  if (tid && !(currentTab && currentTab.id === tid)) switchTab(tid, true);
};

window.addEventListener("popstate", () => {
  const t = tabFromLocation();
  if (t && (!currentTab || currentTab.id !== t.id)) switchTab(t.id, false);
});

function switchTab(tabId, updateHash) {
  const tab = TABS.find(t => t.id === tabId);
  if (!tab) return;
  currentTab = tab;
  if (updateHash) {
    history.pushState(null, "", tabPath(tab));
  }
  document.title = `${tab.name} | Monitor de Ofertas · Descuentos en Chile`;
  document.querySelectorAll(".tab-btn").forEach(b => {
    const isAct = b.dataset.tab === tabId;
    b.classList.toggle("active", isAct);
    if (isAct) b.scrollIntoView({ behavior: "smooth", inline: "nearest", block: "nearest" });
  });

  // Actualizar hero y textos
  $("hero-title").innerHTML = tab.headline || `Los mejores descuentos en ${esc(tab.name)}, <em>actualizados hoy.</em>`;
  $("hero-desc").textContent = tab.subtitle || `Monitoreo de precios y descuentos en ${tab.name}.`;
  $("col-item").dataset.l = tab.item_label || "Producto";
  $("col-item").textContent = $("col-item").dataset.l;
  $("q").placeholder = `Buscar en ${tab.name}...`;

  if (tabCache[tabId]) {
    initTabData(tab, tabCache[tabId]);
  } else {
    $("body").innerHTML = `<tr><td colspan="8"><div class="msg">Cargando ofertas de ${esc(tab.name)}…</div></td></tr>`;
    fetch(tab.file || `${tabId}.json`, { cache: "no-cache" })
      .then(r => { if (!r.ok) throw 0; return r.json(); })
      .then(j => {
        tabCache[tabId] = j;
        initTabData(tab, j);
      })
      .catch(err => {
        $("body").innerHTML = `<tr><td colspan="8"><div class="msg">Error al cargar datos de ${esc(tab.name)}.</div></td></tr>`;
      });
  }
}

const fixUrl = b => {
  if (!b.url) return "";
  if (/buscalibre/i.test(b.tienda) && !/^https?:/i.test(b.url)) {
    return new URL(b.url, "https://www.buscalibre.cl/").href;
  }
  return b.url;
};

// Al ir al detalle se guarda cómo estaba el listado (búsqueda, filtros, página, orden, scroll)
// para dejarlo igual cuando la persona vuelve.
function saveList() {
  if (!currentTab) return;
  try {
    sessionStorage.setItem("lista:" + currentTab.id, JSON.stringify({ q: $("q").value, min: $("min").value, per: $("per").value, page, store, key, dir, y: window.scrollY }));
  } catch (e) {}
}
function restoreList(tabId) {
  let s = null;
  try {
    s = JSON.parse(sessionStorage.getItem("lista:" + tabId) || "null");
    sessionStorage.removeItem("lista:" + tabId);
  } catch (e) {}
  if (!s) return null;
  $("q").value = s.q || "";
  $("min").value = s.min || "0";
  $("per").value = s.per || "10";
  page = s.page || 1;
  store = s.store ?? -1;
  key = s.key || key;
  dir = s.dir || dir;
  if (D.length > 1) document.querySelectorAll(".chip").forEach(c => c.classList.toggle("on", +c.dataset.i === store));
  return s;
}
document.addEventListener("click", e => { if (e.target.closest("a.dl")) saveList(); });

function initTabData(tab, j) {
  D = j.dominios || [];
  document.querySelector("table").classList.toggle("single", D.length < 2);
  const items = j.items || j.libros || [];
  B = items.map(b => ({ ...b, ahorro: (b.promedio || 0) - (b.precio || 0), url: fixUrl(b) }));
  maxD = Math.max(...B.map(b => b.descuento || 0), 1);
  if (j.generated_at) {
    $("upd").textContent = "Actualizado " + new Date(j.generated_at).toLocaleString("es-CL", { dateStyle: "medium", timeStyle: "short" });
  }
  $("s1").firstChild.textContent = B.length;
  $("s2").firstChild.textContent = D.length || 1;
  $("s3").firstChild.textContent = Math.round(B.reduce((a, b) => a + (b.descuento || 0), 0) / Math.max(B.length, 1)) + "%";

  // Chips de tiendas del tab
  if (D.length > 1) {
    $("chips").style.display = "flex";
    $("chips").innerHTML = [{ name: "Todas" }, ...D].map((d, i) =>
      `<button class="chip${i ? "" : " on"}" data-i="${i ? d.id : -1}">${i ? `<i style="--c:${COLORS[(i - 1) % COLORS.length]}"></i>` : ""}${esc(d.name)}</button>`
    ).join("");
  } else if (D.length === 1) {
    $("chips").style.display = "flex";
    $("chips").innerHTML = `<button class="chip on" data-i="${D[0].id}"><i style="--c:${COLORS[0]}"></i>${esc(D[0].name)}</button>`;
  } else {
    $("chips").style.display = "none";
    $("chips").innerHTML = "";
  }

  page = 1;
  store = -1;
  const sv = restoreList(tab.id);
  render();
  if (sv) requestAnimationFrame(() => window.scrollTo(0, sv.y || 0));
}

const col = id => {
  const idx = D.findIndex(d => d.id === id);
  return COLORS[Math.max(idx, 0) % COLORS.length];
};

$("chips").onclick = e => {
  const b = e.target.closest(".chip");
  if (!b) return;
  store = +b.dataset.i;
  document.querySelectorAll(".chip").forEach(c => c.classList.toggle("on", c === b));
  page = 1;
  render();
};

["q", "min", "per"].forEach(id => $(id).addEventListener("input", () => { page = 1; render(); }));

document.querySelector("thead").onclick = e => {
  const h = e.target.closest("th[data-k]");
  if (!h) return;
  const k = h.dataset.k;
  dir = key === k ? -dir : (k === "title" || k === "tienda" || k === "precio" || k === "promedio" ? 1 : -1);
  key = k;
  page = 1;
  render();
};

// Mini gráfico del historial de precios (solo muestra la tendencia; el número que manda es el descuento).
function spark(b) {
  const h = b.hist;
  if (!h || h.length < 2) return `<span class="o">—</span>`;
  const min = Math.min(...h), max = Math.max(...h), rng = max - min, W = 130, H = 40, P = 4;
  const pts = h.map((v, i) => `${(i / (h.length - 1) * W).toFixed(1)},${(rng === 0 ? H / 2 : H - P - (v - min) / rng * (H - 2 * P)).toFixed(1)}`).join(" ");
  const c = "var(--dn)";
  const tip = `Normal ${clp(b.promedio)} · mín ${clp(min)} · máx ${clp(max)} · ${h.length} registros`;
  return `<div class="sp" title="${tip}"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><polygon points="${pts} ${W},${H} 0,${H}" fill="${c}" opacity=".13"/><polyline points="${pts}" fill="none" stroke="${c}" stroke-width="1.8" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg></div>`;
}

function render() {
  const q = $("q").value.toLowerCase().trim(), m = +$("min").value, PER = +$("per").value;
  const L = B.filter(b => (store < 0 || b.dominio_id === store) && b.descuento >= m && (!q || (b.title || "").toLowerCase().includes(q)));
  L.sort((a, b) => (typeof a[key] === "string" ? a[key].localeCompare(b[key], "es") : a[key] - b[key]) * dir);
  document.querySelectorAll("th[data-k]").forEach(h => {
    h.dataset.l = h.dataset.l || h.textContent;
    h.classList.toggle("s", h.dataset.k === key);
    h.textContent = h.dataset.l + (h.dataset.k === key ? (dir > 0 ? " ↑" : " ↓") : "");
  });
  const pages = Math.max(1, Math.ceil(L.length / PER));
  page = Math.min(page, pages);
  const st = (page - 1) * PER;
  $("count").innerHTML = `<b>${L.length}</b> ofertas · mostrando ${L.length ? st + 1 : 0}–${Math.min(st + PER, L.length)}`;

  $("body").innerHTML = L.length ? L.slice(st, st + PER).map((b, i) => `<tr>
    <td class="n">${st + i + 1}</td>
    <td>${b.slug ? `<a class="bk dl" href="/producto/${esc(b.slug)}/">` : `<div class="bk">`}<div class="mc">${b.thumbnail ? `<img src="${esc(b.thumbnail)}" alt="" loading="lazy" decoding="async" onerror="this.remove()">` : "📦"}</div><div class="t">${esc(b.title)}</div>${b.slug ? "</a>" : "</div>"}</td>
    <td class="c-store"><span class="st" style="--c:${col(b.dominio_id)}"><i></i>${esc(b.tienda)}</span></td>
    <td class="r p">${clp(b.precio)}</td>
    <td><div class="d${b.descuento >= 45 ? " hot" : ""}"><b>−${Math.round(b.descuento)}%</b><span><i style="width:${b.descuento / maxD * 100}%"></i></span></div></td>
    <td class="r sv">${clp(b.ahorro)}</td>
    <td class="r">${spark(b)}</td>
    <td class="r">${b.url ? `<a class="btn" href="${esc(b.url)}" target="_blank" rel="noopener" data-id="${b.id}" data-pos="${st + i + 1}">Ir a la tienda ↗</a>` : ""}</td>
  </tr>`).join("") : `<tr><td colspan="8"><div class="msg">Nada por aquí… prueba otra búsqueda 🔍</div></td></tr>`;

  const bt = (l, p, x = "") => `<button ${x} data-p="${p}">${l}</button>`, a = [bt("←", page - 1, page === 1 ? "disabled" : "")];
  const set = [...new Set([1, 2, page - 1, page, page + 1, pages - 1, pages])].filter(x => x >= 1 && x <= pages).sort((x, y) => x - y);
  set.forEach((x, i) => {
    if (i && x - set[i - 1] > 1) a.push("<span>…</span>");
    a.push(bt(x, x, x === page ? 'class="on"' : ""));
  });
  a.push(bt("→", page + 1, page === pages ? "disabled" : ""));
  $("pager").innerHTML = a.join("");
}

$("pager").onclick = e => {
  const b = e.target.closest("button");
  if (!b || b.disabled) return;
  page = +b.dataset.p;
  render();
  $("count").scrollIntoView({ behavior: "smooth" });
};

document.addEventListener("click", e => {
  const a = e.target.closest("a.btn[data-id]");
  if (!a || typeof gtag !== "function") return;
  const b = B.find(x => String(x.id) === a.dataset.id);
  if (!b) return;
  gtag("event", "select_item", {
    item_list_name: currentTab ? currentTab.name : "Ofertas",
    currency: "CLP",
    items: [{ item_id: String(b.id), item_name: b.title, item_brand: b.tienda, price: Math.round(b.precio), discount: Math.round(b.ahorro), index: +a.dataset.pos }]
  });
});
</script>
</body>
</html>
"""


def render_html_page(tab_info, json_ld_obj, default_tab_id):
    title = f"{tab_info['name']} | Monitor de Ofertas · Descuentos en Chile"
    desc = tab_info.get("subtitle", "Comparamos precios y descuentos reales todos los días.")
    hero_title = tab_info.get("headline", f"Los mejores descuentos en {tab_info['name']}")
    hero_subtitle = tab_info.get("subtitle", "")
    item_label = tab_info.get("item_label", "Producto")
    ld_json_str = json.dumps(json_ld_obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    html = HTML_TEMPLATE
    html = html.replace("__PAGE_TITLE__", title)
    html = html.replace("__PAGE_DESC__", desc)
    html = html.replace("__HERO_TITLE__", hero_title)
    html = html.replace("__HERO_SUBTITLE__", hero_subtitle)
    html = html.replace("__ITEM_LABEL__", item_label)
    html = html.replace("__JSON_LD_CONTENT__", ld_json_str)
    path = "/" if tab_info.get("html_file", "index.html") == "index.html" else "/" + tab_info["html_file"].rsplit(".", 1)[0]
    canonical = f'<link rel="canonical" href="{SITE_URL}{path}">' if SITE_URL else ""
    html = html.replace("__CANONICAL_TAG__", canonical)
    html = html.replace("__DEFAULT_TAB_ID__", default_tab_id)
    return html


MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _formatear_dt(dt):
    return f"{dt.day} {MESES_ES[dt.month - 1]}. {dt.year}, {dt.strftime('%H:%M')}"


def _formatear_fecha(dt):
    return f"{dt.day:02d}/{dt.month:02d}/{dt.year}"


def _ts_a_fecha(ts, tz_cl):
    try:
        return _formatear_fecha(datetime.fromtimestamp(ts, tz=tz_cl))
    except Exception:
        return ""


def _fmt_unidad(val, unidad):
    pre = unidad.get("pre", "$")
    suf = unidad.get("suf", "")
    v = int(round(val))
    signo = "−" if v < 0 else ""
    n = f"{abs(v):,}".replace(",", ".")
    return f"{signo}{pre}{n}{suf}"


def _pct_str(val, dec=1):
    if round(val, dec) == 0:
        return "0%"
    signo = "+" if val > 0 else "−"
    fmt_n = f"{abs(val):.{dec}f}".replace(".", ",")
    return f"{signo}{fmt_n}%"


ICON_BACK_SVG = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M19 12H5M11 6l-6 6 6 6"/></svg>'


def render_producto_page(item, tab_cfg, detalle_data, now_iso):
    tz_cl = ZoneInfo("America/Santiago")
    series = detalle_data.get("s", [])
    vs = [p[1] for p in series] if series else [round(item.get("precio", 0))]
    mn = min(vs)
    mx = max(vs)
    p_min = next((p for p in series if p[1] == mn), None)
    p_max = next((p for p in series if p[1] == mx), None)
    min_dt_str = _ts_a_fecha(p_min[0], tz_cl) if p_min else ""
    max_dt_str = _ts_a_fecha(p_max[0], tz_cl) if p_max else ""
    first_dt_str = _ts_a_fecha(series[0][0], tz_cl) if series else ""
    last_dt_str = _ts_a_fecha(series[-1][0], tz_cl) if series else ""

    prom = round(item.get("promedio") or 0)
    precio = round(item.get("precio", 0))
    dev = (precio - prom) / prom * 100 if prom else 0
    dev_str = _pct_str(dev)

    unidad = tab_cfg.get("unidad", {"pre": "$", "suf": ""})
    descuento = item.get("descuento", 0)

    slug = item["slug"]
    canonical_url = f"{SITE_URL}/producto/{slug}/"
    tab_html = tab_cfg.get("html_file", "index.html")
    tab_path = "/" if tab_html == "index.html" else "/" + tab_html
    tab_name = tab_cfg.get("name", "Listado")

    try:
        dt_gen = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00")).astimezone(tz_cl)
        fecha_gen_str = _formatear_dt(dt_gen)
    except Exception:
        fecha_gen_str = now_iso

    ultimos = detalle_data.get("r", [])
    filas_html = []
    for i, p in enumerate(ultimos[:DET_REGISTROS]):
        try:
            dt = datetime.fromtimestamp(p[0], tz=tz_cl)
            fecha_col = _formatear_fecha(dt)
            hora_col = dt.strftime("%H:%M")
        except Exception:
            fecha_col = "—"
            hora_col = "—"
        val_col = _fmt_unidad(p[1], unidad)
        if i + 1 < len(ultimos):
            pv = ultimos[i + 1][1]
            dl = _pct_str((p[1] - pv) / pv * 100) if pv else "—"
        else:
            dl = "—"
        mut_cls = ' class="mut"' if dl in ("0%", "—") else ''
        filas_html.append(f'<div class="gr"><span>{fecha_col}</span><span class="mut">{hora_col}</span><span style="font-weight:600">{val_col}</span><span{mut_cls}>{dl}</span></div>')
    filas_str = "\n".join(filas_html)

    if item.get("thumbnail"):
        img_html = f'<img src="{html.escape(item["thumbnail"])}" alt="{html.escape(item["title"])}" loading="eager">'
    else:
        img_html = '📦'

    ld_prod = {
        "@context": "https://schema.org/",
        "@type": "Product",
        "name": item["title"],
        "description": f"Historial de precios y análisis de oferta para {item['title']} en {item.get('tienda', '')}. Precio actual: {_fmt_unidad(precio, unidad)}.",
        "offers": {
            "@type": "Offer",
            "price": str(precio),
            "priceCurrency": "CLP",
            "availability": "https://schema.org/InStock",
            "url": item.get("url") or canonical_url,
        }
    }
    if item.get("thumbnail"):
        t_url = item["thumbnail"]
        if not t_url.startswith("http"):
            t_url = f"{SITE_URL}/{t_url.lstrip('/')}"
        ld_prod["image"] = [t_url]
    ld_str = json.dumps(ld_prod, ensure_ascii=False)

    og_image_tag = f'<meta property="og:image" content="{html.escape(ld_prod.get("image", [""])[0])}">' if ld_prod.get("image") else ''
    tienda_esc = html.escape(str(item.get("tienda", "")))
    title_esc = html.escape(str(item.get("title", "")))
    desc_meta = f"Historial de precios de {title_esc} en {tienda_esc}. Valor actual: {_fmt_unidad(precio, unidad)}, promedio: {_fmt_unidad(prom, unidad)}."
    store_url_esc = html.escape(str(item.get("url") or "#"))

    page_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<base href="/">
<title>{title_esc} · Historial de precios | Monitor de Ofertas</title>
<meta name="description" content="{html.escape(desc_meta)}">
<link rel="canonical" href="{canonical_url}">
<meta property="og:type" content="product">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{html.escape(desc_meta)}">
<meta property="og:url" content="{canonical_url}">
{og_image_tag}
<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag.js?id=G-CJRB34M9L8"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){{dataLayer.push(arguments);}}
  gtag('js', new Date());
  gtag('config', 'G-CJRB34M9L8');
</script>
<script type="application/ld+json">
{ld_str}
</script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0b0f17;--card:#121a28;--card2:#172235;--ink:#e9eef7;--mute:#8d99ae;--line:#223048;--acc:#ff6b3d;--onacc:#1a0f0a;--tip:#0b0f17}}
@media (prefers-color-scheme:light){{:root{{--bg:#f3f5f9;--card:#fff;--card2:#f8fafc;--ink:#0b0f17;--mute:#5a6578;--line:#dbe1ec;--acc:#ec5a2b;--onacc:#1a0f0a;--tip:#fff}}}}
*{{box-sizing:border-box;margin:0}}
body{{background:var(--bg);color:var(--ink);font:400 14px/1.45 Inter,system-ui,sans-serif;min-height:100vh;background-image:radial-gradient(900px 480px at 90% -10%,rgba(255,107,61,.09),transparent),radial-gradient(700px 380px at -5% 5%,rgba(82,225,155,.06),transparent)}}
.wrap{{max-width:1200px;margin:auto;padding:28px 20px 56px;display:flex;flex-direction:column;gap:24px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:20px}}
.mut{{color:var(--mute)}}
.lbl{{font:600 11px Inter,sans-serif;letter-spacing:.1em;text-transform:uppercase;color:var(--mute)}}
h1,h2{{font-family:Fraunces,Georgia,serif;font-weight:800}}
.btn{{display:inline-flex;align-items:center;gap:8px;min-height:44px;padding:0 18px;border-radius:12px;border:1px solid var(--line);background:transparent;color:var(--ink);font:600 13px Inter,sans-serif;text-decoration:none;cursor:pointer;transition:border-color .15s}}
.btn:hover{{border-color:var(--acc)}}
.btn.pri{{background:var(--acc);border-color:var(--acc);color:var(--onacc)}}
.pill{{display:inline-flex;align-items:center;gap:7px;padding:5px 12px;border-radius:99px;background:var(--card2);border:1px solid var(--line);font:500 12px Inter,sans-serif}}
.pill i{{width:8px;height:8px;border-radius:50%;background:var(--acc)}}
.top{{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}}
.top .l{{display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
.head{{padding:24px 28px;display:flex;gap:24px;align-items:center;flex-wrap:wrap}}
.img{{width:96px;height:120px;border-radius:12px;overflow:hidden;background:var(--card2);border:1px solid var(--line);flex:none;display:flex;align-items:center;justify-content:center;font-size:28px}}
.img img{{width:100%;height:100%;object-fit:cover;display:block}}
.head .m{{flex:1;min-width:260px;display:flex;flex-direction:column;gap:10px}}
.head h1{{font-size:clamp(24px,3.5vw,34px);line-height:1.15;letter-spacing:-.02em}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}}
.kpi{{padding:18px 20px;display:flex;flex-direction:column;gap:6px}}
.kv{{font:800 26px Fraunces,serif;letter-spacing:-.01em}}
.kpi.cur{{border-color:var(--acc)}}.kpi.cur .kv{{color:var(--acc)}}
.ch{{padding:24px 24px 20px;display:flex;flex-direction:column;gap:18px}}
.chh{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;flex-wrap:wrap}}
.seg{{display:inline-flex;border:1px solid var(--line);border-radius:12px;padding:3px;gap:2px}}
.seg button{{border:0;background:transparent;color:var(--mute);min-height:38px;padding:0 14px;border-radius:9px;font:600 12px Inter,sans-serif;cursor:pointer}}
.seg button.on{{background:var(--ink);color:var(--bg)}}
.plot{{display:flex;gap:14px}}
.ya{{width:72px;position:relative;height:300px;font-size:11px;flex:none;color:var(--mute)}}
.ya span{{position:absolute;right:0;transform:translateY(-50%)}}
.cv{{position:relative;flex:1;height:300px;cursor:crosshair;touch-action:pan-y}}
.cv svg{{width:100%;height:300px;display:block}}
.dot{{position:absolute;width:12px;height:12px;margin:-6px 0 0 -6px;border-radius:50%;background:var(--bg);border:3px solid var(--acc);pointer-events:none}}
.lb{{position:absolute;font-size:11px;pointer-events:none;white-space:nowrap}}
#cr{{position:absolute;top:0;bottom:0;width:1px;background:var(--mute);opacity:.6;display:none;pointer-events:none}}
.tip{{position:absolute;top:8px;min-width:170px;padding:10px 12px;background:var(--tip);border:1px solid var(--line);border-radius:10px;pointer-events:none;box-shadow:0 10px 30px -10px rgba(0,0,0,.5);display:none;line-height:1.5}}
.xa{{display:flex;justify-content:space-between;margin-left:86px;font-size:11px;color:var(--mute)}}
.tb{{overflow:hidden}}
.tbh{{padding:16px 22px;border-bottom:1px solid var(--line)}}
.gr{{display:grid;grid-template-columns:1.2fr 1fr 1fr 1.4fr;gap:14px;padding:13px 22px;border-bottom:1px solid var(--line);font-size:13px}}
.gr:last-child{{border-bottom:0}}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="l">
      <a class="btn" href="{tab_path}" data-back>{ICON_BACK_SVG}Volver</a>
      <nav class="mut" aria-label="Ruta">Monitor de Ofertas / <a href="{tab_path}" style="color:inherit;text-decoration:none">{html.escape(tab_name)}</a> / <span style="color:var(--ink)">Detalle</span></nav>
    </div>
    <span class="pill mut">Actualizado {fecha_gen_str}</span>
  </div>

  <div class="card head">
    <div class="img">{img_html}</div>
    <div class="m">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span class="pill"><i></i>{tienda_esc}</span>
        {f'<span class="pill" style="color:var(--acc);font-weight:700">−{round(descuento)}% vs. promedio</span>' if descuento > 0 else ''}
      </div>
      <h1>{title_esc}</h1>
    </div>
    <a class="btn pri" id="go" href="{store_url_esc}" target="_blank" rel="noopener">Ir a la tienda ↗</a>
  </div>

  <div class="kpis">
    <div class="card kpi cur">
      <span class="lbl">Valor actual</span>
      <span class="kv">{_fmt_unidad(precio, unidad)}</span>
      <span class="mut" style="font-size:12px">{f'Último registro ({last_dt_str})' if last_dt_str else 'Último registro'}</span>
    </div>
    <div class="card kpi">
      <span class="lbl">Promedio histórico</span>
      <span class="kv">{_fmt_unidad(prom, unidad)}</span>
      <span class="mut" style="font-size:12px">Promedio consolidado</span>
    </div>
    <div class="card kpi">
      <span class="lbl">Mínimo histórico</span>
      <span class="kv">{_fmt_unidad(mn, unidad)}</span>
      <span class="mut" style="font-size:12px">{f'Registrado el {min_dt_str}' if min_dt_str else 'Mínimo registrado'}</span>
    </div>
    <div class="card kpi">
      <span class="lbl">Máximo histórico</span>
      <span class="kv">{_fmt_unidad(mx, unidad)}</span>
      <span class="mut" style="font-size:12px">{f'Registrado el {max_dt_str}' if max_dt_str else 'Máximo registrado'}</span>
    </div>
    <div class="card kpi">
      <span class="lbl">Variación vs. promedio</span>
      <span class="kv">{dev_str}</span>
      <span class="mut" style="font-size:12px">Respecto a {_fmt_unidad(prom, unidad)}</span>
    </div>
    <div class="card kpi">
      <span class="lbl">Total registros</span>
      <span class="kv">{detalle_data.get('n', len(series))}</span>
      <span class="mut" style="font-size:12px">{f'Desde {first_dt_str} a {last_dt_str}' if first_dt_str and last_dt_str else 'Observaciones registradas'}</span>
    </div>
  </div>

  <div class="card ch">
    <div class="chh">
      <div>
        <span class="lbl">Historial del valor</span>
        <h2>Evolución en el tiempo</h2>
      </div>
      <div class="seg">
        <button data-r="7">7 d</button>
        <button data-r="30">30 d</button>
        <button data-r="90">90 d</button>
        <button class="on" data-r="all">Todo</button>
      </div>
    </div>
    <div class="plot">
      <div class="ya" id="ya"></div>
      <div class="cv" id="cv">
        <svg id="sv" viewBox="0 0 1000 300" preserveAspectRatio="none"></svg>
        <div class="dot" id="cd" style="display:none"></div>
        <div id="cr"></div>
        <div class="tip" id="tip"></div>
        <div id="ov"></div>
      </div>
    </div>
    <div class="xa" id="xa"></div>
  </div>

  <div class="card tb">
    <div class="tbh">
      <span class="lbl">Detalle cronológico</span>
      <h2>Últimos registros</h2>
    </div>
    <div class="gr mut" style="font-weight:600;font-size:11px;letter-spacing:.05em;text-transform:uppercase">
      <span>Fecha</span><span>Hora</span><span>Valor</span><span>Variación</span>
    </div>
    {filas_str}
  </div>
</div>

<script>
const $ = id => document.getElementById(id),
  U = {json.dumps(unidad)},
  S = {json.dumps(detalle_data.get('s', []))},
  prom = {prom},
  ID = "{item['id']}",
  E = {json.dumps({"title": item["title"], "tienda": item.get("tienda", ""), "precio": precio})};
let range = "all", VIEW = null;

const fmt = v => U.pre + Math.round(v).toLocaleString("es-CL") + U.suf;
const fd = ts => new Date(ts * 1000).toLocaleDateString("es-CL", {{ day: "2-digit", month: "2-digit", year: "numeric" }});
const fs = ts => new Date(ts * 1000).toLocaleDateString("es-CL", {{ day: "2-digit", month: "2-digit" }});
const pct = (x, dec = 1) => {{ const k = 10 ** dec, r = Math.round(x * k) / k; return r === 0 ? "0%" : (r > 0 ? "+" : "−") + Math.abs(r).toLocaleString("es-CL", {{ maximumFractionDigits: dec }}) + "%"; }};
const nice = x => {{ const p = 10 ** Math.floor(Math.log10(x)), n = x / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }};

function draw() {{
  if (!S || S.length < 2) return;
  const lastT = S[S.length - 1][0], firstT = S[0][0], days = {{ "7": 7, "30": 30, "90": 90, all: 0 }}[range];
  const cut = days ? Math.max(firstT, lastT - days * 86400) : firstT;
  let pts = S.filter(p => p[0] >= cut);
  const prev = S.filter(p => p[0] < cut).pop();
  if (prev && (!pts.length || pts[0][0] > cut)) pts.unshift([cut, prev[1]]);
  if (pts[pts.length - 1][0] < lastT) pts.push([lastT, pts[pts.length - 1][1]]);
  const t0 = pts[0][0], t1 = Math.max(pts[pts.length - 1][0], t0 + 1);
  const vals = pts.map(p => p[1]).concat(prom ? [prom] : []);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.1 || hi * 0.1 || 1, y0 = lo - pad, y1 = hi + pad;
  const X = t => (t - t0) / (t1 - t0) * 1000, Y = v => 20 + (y1 - v) / (y1 - y0) * 260;
  const step = nice((y1 - y0) / 4), ticks = [];
  for (let v = Math.ceil(y0 / step) * step; v <= y1; v += step) ticks.push(v);

  let d = `M${{X(pts[0][0]).toFixed(1)}} ${{Y(pts[0][1]).toFixed(1)}}`;
  for (let i = 1; i < pts.length; i++) d += `H${{X(pts[i][0]).toFixed(1)}}V${{Y(pts[i][1]).toFixed(1)}}`;
  $("sv").innerHTML = `<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(--acc);stop-opacity:.3"/><stop offset="1" style="stop-color:var(--acc);stop-opacity:0"/></linearGradient></defs>
    <path d="${{ticks.map(v => `M0 ${{Y(v).toFixed(1)}}H1000`).join("")}}" stroke="var(--line)" fill="none" vector-effect="non-scaling-stroke"/>
    <path d="${{d}}V300H0Z" fill="url(#g)"/>
    ${{prom ? `<path d="M0 ${{Y(prom).toFixed(1)}}H1000" stroke="var(--mute)" stroke-dasharray="6 6" fill="none" vector-effect="non-scaling-stroke"/>` : ""}}
    <path d="${{d}}" fill="none" stroke="var(--acc)" stroke-width="2" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
  $("ya").innerHTML = ticks.map(v => `<span style="top:${{Y(v).toFixed(1)}}px">${{fmt(v)}}</span>`).join("");

  const cur = pts[pts.length - 1][1], mxp = pts.reduce((a, p) => p[1] > a[1] ? p : a), ov = [];
  if (prom) ov.push(`<span class="lb mut" style="left:8px;top:${{(Y(prom) + 6).toFixed(0)}}px">Promedio ${{fmt(prom)}}</span>`);
  if (mxp[1] !== cur) {{
    const lx = (X(mxp[0]) / 10).toFixed(2);
    ov.push(`<span class="dot" style="left:${{lx}}%;top:${{Y(mxp[1]).toFixed(1)}}px"></span><span class="lb" style="left:calc(${{lx}}% + 12px);top:${{Math.max(0, Y(mxp[1]) - 22).toFixed(0)}}px">Máx ${{fmt(mxp[1])}}</span>`);
  }}
  ov.push(`<span class="dot" style="left:100%;top:${{Y(cur).toFixed(1)}}px"></span><span class="lb" style="right:16px;top:${{Math.max(0, Y(cur) - 26).toFixed(0)}}px">Actual ${{fmt(cur)}}</span>`);
  $("ov").innerHTML = ov.join("");
  $("xa").innerHTML = Array.from({{ length: 5 }}, (_, i) => `<span>${{fs(t0 + (t1 - t0) * i / 4)}}</span>`).join("");
  VIEW = {{ pts, t0, t1, Y }};
}}

document.querySelectorAll(".seg button").forEach(b => b.onclick = () => {{
  range = b.dataset.r;
  document.querySelectorAll(".seg button").forEach(x => x.classList.toggle("on", x === b));
  draw();
}});

const cv = $("cv");
cv.addEventListener("pointermove", e => {{
  if (!VIEW) return;
  const r = cv.getBoundingClientRect(), f = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), t = VIEW.t0 + f * (VIEW.t1 - VIEW.t0);
  let p = VIEW.pts[0];
  for (const q of VIEW.pts) if (q[0] <= t) p = q;
  const cr = $("cr"), cd = $("cd"), tip = $("tip"), left = f < 0.62;
  cr.style.display = cd.style.display = tip.style.display = "block";
  cr.style.left = cd.style.left = (f * 100) + "%";
  cd.style.top = VIEW.Y(p[1]) + "px";
  tip.style.left = left ? `calc(${{f * 100}}% + 14px)` : "auto";
  tip.style.right = left ? "auto" : `calc(${{(1 - f) * 100}}% + 14px)`;
  tip.innerHTML = `<span class="mut" style="font-size:11px">${{fd(t)}}</span><br><b>Valor ${{fmt(p[1])}}</b>${{prom ? `<br><span class="mut" style="font-size:11px">vs. promedio ${{pct((p[1] - prom) / prom * 100, 0)}}</span>` : ""}}`;
}});
cv.addEventListener("pointerleave", () => {{ $("cr").style.display = $("cd").style.display = $("tip").style.display = "none"; }});

document.addEventListener("click", e => {{
  const a = e.target.closest("a[data-back]");
  if (a && !e.metaKey && !e.ctrlKey && !e.shiftKey && e.button === 0) {{
    try {{
      if (history.length > 1 && document.referrer && new URL(document.referrer).origin === location.origin) {{
        e.preventDefault();
        history.back();
      }}
    }} catch (_) {{}}
  }}
  const go = e.target.closest("#go");
  if (go && E && typeof gtag === "function") {{
    gtag("event", "select_item", {{ item_list_name: "Detalle", currency: "CLP", items: [{{ item_id: ID, item_name: E.title, item_brand: E.tienda, price: Math.round(E.precio) }}] }});
  }}
}});

draw();
</script>
</body>
</html>"""
    return page_html


def generar_paginas_producto(active_tabs_data, now_iso):
    """Genera páginas físicas producto/{slug}/index.html para cada producto activo con historial.
    Los productos que dejan de estar disponibles con descuento se conservan indefinidamente (nunca se eliminan)."""
    prod_base_dir = os.path.join(DIR_BASE, "producto")
    os.makedirs(prod_base_dir, exist_ok=True)

    productos_unicos = {}
    for tab in active_tabs_data:
        cfg = tab["config"]
        for b in tab["items"]:
            if b.get("slug") and b.get("_det") and b["id"] not in productos_unicos:
                productos_unicos[b["id"]] = (b, cfg)

    actualizados = 0
    for bid, (b, cfg) in productos_unicos.items():
        slug = b["slug"]
        dir_p = os.path.join(prod_base_dir, slug)
        os.makedirs(dir_p, exist_ok=True)
        html_p = os.path.join(dir_p, "index.html")
        contenido = render_producto_page(b, cfg, b["_det"], now_iso)
        with open(html_p, "w", encoding="utf-8") as f:
            f.write(contenido)
        actualizados += 1

    # Conservar y listar todos los slugs existentes en disco (los actualizados hoy + los históricos anteriores)
    todos_los_slugs = sorted([
        nombre for nombre in os.listdir(prod_base_dir)
        if os.path.isdir(os.path.join(prod_base_dir, nombre))
    ])

    print(f"✓ {actualizados} páginas actualizadas hoy. Total conservadas en producto/: {len(todos_los_slugs)} páginas")
    return todos_los_slugs


def generar_sitemap(active_tabs_data, slugs_productos, fecha):
    """sitemap.xml con pestañas y páginas de producto. Crea robots.txt si no existe."""
    filas = []
    for tab in active_tabs_data:
        html_file = tab["config"].get("html_file", "index.html")
        path = "/" if html_file == "index.html" else "/" + html_file.rsplit(".", 1)[0]
        loc = (SITE_URL + path).replace("&", "&amp;")
        prio = "1.0" if path == "/" else "0.8"
        filas.append(f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{fecha}</lastmod>\n"
                     f"    <changefreq>daily</changefreq>\n    <priority>{prio}</priority>\n  </url>")

    for slug in slugs_productos:
        loc = f"{SITE_URL}/producto/{slug}/".replace("&", "&amp;")
        filas.append(f"  <url>\n    <loc>{loc}</loc>\n    <lastmod>{fecha}</lastmod>\n"
                     f"    <changefreq>daily</changefreq>\n    <priority>0.6</priority>\n  </url>")

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           + "\n".join(filas) + "\n</urlset>\n")
    ruta = os.path.join(DIR_BASE, "sitemap.xml")
    with open(ruta + ".tmp", "w", encoding="utf-8") as f:
        f.write(xml)
    os.replace(ruta + ".tmp", ruta)
    robots = os.path.join(DIR_BASE, "robots.txt")
    if not os.path.exists(robots):
        with open(robots, "w", encoding="utf-8") as f:
            f.write(f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}/sitemap.xml\n")


def guardar_json_atomico(ruta, datos):
    tmp = ruta + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, ruta)


def main():
    if not TOKEN:
        sys.exit("Falta el token: define ONESOURCE_TOKEN en .env o como variable de entorno.")

    print(f"Iniciando generador de ofertas con límite de antigüedad de {MAX_HORAS_UPLOAD}h...")
    nombres = nombres_dominios()
    todos_los_items_para_imagenes = []
    active_tabs_data = []

    for tab_cfg in TABS_CONFIG:
        tab_id = tab_cfg["id"]
        tab_name = tab_cfg["name"]
        print(f"\nProcesando Tab: {tab_name} ({tab_id})...")

        dominios_info = []
        tab_items = []
        max_d = tab_cfg.get("max_descuento", 90.0)
        por_dom = tab_cfg.get("por_dominio", 100)

        for d in tab_cfg["domains"]:
            dom_name = nombres.get(d, f"dominio {d}")
            up = ultimo_upload(d)
            if not up:
                print(f"  ✗ {dom_name} ({d}): descartado (sin upload válido < {MAX_HORAS_UPLOAD}h)")
                continue

            try:
                raw_items = mejores_del_dominio(up["id"], por_dom)
            except Exception as e:
                print(f"  ✗ {dom_name} ({d}): error al leer featured ({e})")
                continue

            validos = []
            for item in raw_items:
                norm = normalizar(item, d, dom_name)
                # Filtros de sanidad: precio válido, descuento positivo y menor que max_descuento,
                # y promedio no superior a 30 veces el precio (excluye errores de scraper).
                pr = norm.get("precio") or 0
                prom = norm.get("promedio") or 0
                desc = norm.get("descuento") or 0
                if pr > 0 and 0 < desc <= max_d and prom <= pr * 30:
                    validos.append(norm)

            tab_items += validos
            dominios_info.append({
                "id": d,
                "name": dom_name,
                "upload_id": up["id"],
                "upload_created_at": up.get("created_at"),
                "traidos": len(validos)
            })
            print(f"  ✓ {dom_name} ({d}): upload {up['id']} -> {len(validos)} ofertas válidas")

        if not tab_items:
            print(f"  ⚠ Tab {tab_name} no tiene ofertas válidas. Omitiendo.")
            continue

        tab_items.sort(key=lambda b: (-b["descuento"], b["precio"]))
        tab_items = tab_items[:tab_cfg.get("top", 100)]
        todos_los_items_para_imagenes += tab_items

        active_tabs_data.append({
            "config": tab_cfg,
            "dominios": dominios_info,
            "items": tab_items,
            "avg_discount": round(sum(b["descuento"] for b in tab_items) / len(tab_items)),
        })

    if not active_tabs_data:
        sys.exit("Error: No se generó ningún tab con ofertas válidas.")

    # Descarga unificada de imágenes (evita borrar imágenes entre tabs)
    print(f"\nDescargando imágenes de {len(todos_los_items_para_imagenes)} ofertas activas...")
    guardar_imagenes(todos_los_items_para_imagenes)

    print("\nDescargando historial de precios...")
    guardar_historiales(todos_los_items_para_imagenes)

    # Preparar tabs.json metadata
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tabs_meta = []
    for tab in active_tabs_data:
        cfg = tab["config"]
        tabs_meta.append({
            "id": cfg["id"],
            "name": cfg["name"],
            "icon": cfg.get("icon", "🏷️"),
            "headline": cfg.get("headline", ""),
            "subtitle": cfg.get("subtitle", ""),
            "item_label": cfg.get("item_label", "Producto"),
            "file": cfg.get("json_file", f"{cfg['id']}.json"),
            "html_file": cfg.get("html_file", f"{cfg['id']}.html"),
            "path": "/" if cfg.get("html_file", "index.html") == "index.html" else "/" + cfg["html_file"].rsplit(".", 1)[0],
            "total": len(tab["items"]),
            "avg_discount": tab["avg_discount"],
            "dominios": tab["dominios"],
        })

    tabs_json_path = os.path.join(DIR_BASE, "tabs.json")
    guardar_json_atomico(tabs_json_path, {
        "generated_at": now_iso,
        "total_tabs": len(tabs_meta),
        "tabs": tabs_meta,
    })
    print(f"✓ Guardado tabs.json con {len(tabs_meta)} tabs.")

    # Guardar JSON individual de cada tab
    for tab in active_tabs_data:
        cfg = tab["config"]
        json_path = os.path.join(DIR_BASE, cfg["json_file"])
        out_payload = {
            "id": cfg["id"],
            "name": cfg["name"],
            "icon": cfg.get("icon", "🏷️"),
            "headline": cfg.get("headline", ""),
            "subtitle": cfg.get("subtitle", ""),
            "item_label": cfg.get("item_label", "Producto"),
            "generated_at": now_iso,
            "total": len(tab["items"]),
            "avg_discount": tab["avg_discount"],
            "dominios": tab["dominios"],
            "items": [{k: v for k, v in b.items() if v is not None and not k.startswith("_")} for b in tab["items"]],
        }
        # Para compatibilidad exacta con la versión previa de libros.json:
        if cfg["id"] == "libros":
            out_payload["por_dominio"] = cfg.get("por_dominio", 250)
            out_payload["libros"] = out_payload["items"]

        guardar_json_atomico(json_path, out_payload)
        print(f"✓ Guardado {cfg['json_file']} ({len(tab['items'])} items).")

        # Datos de la página de detalle (/detalle?tab=...&id=...): un archivo por tab
        detalles = {}
        for b in tab["items"]:
            d = b.get("_det")
            if d:
                detalles[str(b["id"])] = {
                    "title": b["title"], "tienda": b["tienda"], "url": b.get("url"),
                    "thumbnail": b.get("thumbnail"), "precio": b["precio"],
                    "promedio": b["promedio"], **d,
                }
        os.makedirs(os.path.join(DIR_BASE, "detalle"), exist_ok=True)
        guardar_json_atomico(os.path.join(DIR_BASE, "detalle", f"{cfg['id']}.json"), {
            "id": cfg["id"],
            "name": cfg["name"],
            "path": "/" if cfg.get("html_file", "index.html") == "index.html" else "/" + cfg["html_file"].rsplit(".", 1)[0],
            "unidad": cfg.get("unidad", {"pre": "$", "suf": ""}),
            "generated_at": now_iso,
            "items": detalles,
        })
        print(f"✓ Guardado detalle/{cfg['id']}.json ({len(detalles)} con historial).")

    # Generar archivos HTML (index.html + páginas dedicadas por tab)
    print("\nGenerando páginas HTML...")
    for tab in active_tabs_data:
        cfg = tab["config"]
        ld_obj = generar_json_ld(tab["items"], cfg)

        # Generar su archivo HTML dedicado si es diferente a index.html
        if cfg.get("html_file") and cfg["html_file"] != "index.html":
            html_content = render_html_page(cfg, ld_obj, default_tab_id=cfg["id"])
            h_path = os.path.join(DIR_BASE, cfg["html_file"])
            with open(h_path, "w", encoding="utf-8") as f:
                f.write(html_content)
            print(f"✓ Generado {cfg['html_file']}")

    # Generar index.html principal (con tab 'libros' por defecto si existe, o el primero)
    primary_tab = next((t for t in active_tabs_data if t["config"]["id"] == "libros"), active_tabs_data[0])
    primary_cfg = primary_tab["config"]
    primary_ld = generar_json_ld(primary_tab["items"], primary_cfg)
    index_html_content = render_html_page(primary_cfg, primary_ld, default_tab_id=primary_cfg["id"])
    index_path = os.path.join(DIR_BASE, "index.html")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(index_html_content)
    print(f"✓ Generado index.html principal.")

    # Generar páginas físicas SEO de cada producto con historial
    print("\nGenerando páginas de producto físicas (SEO /producto/<id-slug>/)...")
    slugs_productos = generar_paginas_producto(active_tabs_data, now_iso)

    generar_sitemap(active_tabs_data, slugs_productos, now_iso[:10])
    print(f"✓ Generado sitemap.xml con {len(active_tabs_data) + len(slugs_productos)} URLs.")

    print("\n¡Proceso completado con éxito! Todos los tabs y páginas HTML fueron generados.")


if __name__ == "__main__":
    main()
