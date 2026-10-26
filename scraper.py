# scraper.py

from requests_html import HTMLSession
from bs4 import BeautifulSoup
import re
import pyppeteer  # Import implícito necesario para requests-html.render()

PRICE_RE = re.compile(r"S/\s*([\d,]+\.?\d*)")

# Etiquetas que puede usar la tienda (en minúsculas). Si Inkafarma las cambia
# otra vez, basta con agregar el texto nuevo aquí.
LABELS_REGULAR = ("precio regular",)
LABELS_PROMO = ("precio promocional", "precio online")
LABELS_OH = ("exclusivo con tarjeta", "exclusivo oh", "exclusivo con sip")


def get_price_value(price_text):
    if price_text:
        match = PRICE_RE.search(price_text)
        if match:
            return float(match.group(1).replace(",", ""))
    return None


def _find_price_after_label(lines, labels, lookahead=3):
    """Busca la primera línea que contenga alguna etiqueta y devuelve el primer
    precio 'S/ xx.xx' que aparezca en esa línea (después de la etiqueta) o en
    las siguientes `lookahead` líneas. Devuelve el texto del precio o None."""
    for i, line in enumerate(lines):
        low = line.lower()
        for label in labels:
            pos = low.find(label)
            if pos == -1:
                continue
            # 1) precio en la misma línea, después de la etiqueta
            m = PRICE_RE.search(line[pos + len(label):])
            if m:
                return m.group(0)
            # 2) precio en las líneas siguientes
            for nxt in lines[i + 1:i + 1 + lookahead]:
                m = PRICE_RE.search(nxt)
                if m:
                    return m.group(0)
            break
    return None


def extract_product_details_from_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")

    # ---- Nombre ----
    product_name = "Producto Desconocido"
    name_tag = soup.select_one("h1.product-detail-information__name") or soup.find("h1")
    if name_tag and name_tag.get_text(strip=True):
        product_name = name_tag.get_text(strip=True)
    else:
        meta_title = soup.find("meta", property="og:title")
        if meta_title and meta_title.get("content"):
            product_name = (
                meta_title["content"]
                .replace("Inkafarma: Más salud al mejor precio | ", "")
                .replace("Inkafarma | ", "")
                .strip()
            )
            if " - Inkafarma" in product_name:
                product_name = product_name.split(" - Inkafarma")[0]

    # ---- Precios (por texto visible, sin depender de clases CSS) ----
    lines = [l for l in soup.get_text("\n", strip=True).split("\n") if l.strip()]

    texto_regular = _find_price_after_label(lines, LABELS_REGULAR)
    texto_promo = _find_price_after_label(lines, LABELS_PROMO)
    texto_oh = _find_price_after_label(lines, LABELS_OH)

    regular_value = get_price_value(texto_regular)
    promo_value = get_price_value(texto_promo)
    oh_value = get_price_value(texto_oh)

    # main.py exige que el regular esté "tachado" para considerar la promo.
    # Ya no dependemos de la clase 'text-strike': hay descuento si algún
    # precio de oferta es menor que el regular.
    ofertas = [v for v in (promo_value, oh_value) if v is not None]
    regular_esta_tachado = bool(
        regular_value is not None and ofertas and min(ofertas) < regular_value
    )

    return {
        "name": product_name,
        "regular_price_text": texto_regular,
        "promo_price_text": texto_promo,
        "oh_price_text": texto_oh,
        "is_regular_price_striked": regular_esta_tachado,
        "regular_price_value": regular_value,
        "promo_price_value": promo_value,
        "oh_price_value": oh_value,
    }


def fetch_product_data(product_url, headers):
    session = HTMLSession()
    product_data = None
    try:
        print(f"Scraper: Intentando acceder a: {product_url}")
        response = session.get(product_url, headers=headers, timeout=35)
        response.raise_for_status()
        print("Scraper: Página obtenida. Renderizando JavaScript...")
        response.html.render(timeout=45, scrolldown=2, sleep=5)
        print("Scraper: JavaScript renderizado. Extrayendo detalles del producto...")
        rendered_html = response.html.html
        product_data = extract_product_details_from_html(rendered_html)
        product_data["url"] = product_url
        # Diagnóstico: si no se halló ningún precio, lo dejamos en el log
        if product_data["regular_price_value"] is None:
            print(f"Scraper: ADVERTENCIA, no se encontró ningún precio en {product_url}. "
                  "Es probable que la estructura de la página haya cambiado o que haya un bloqueo.")
    except pyppeteer.errors.TimeoutError as e:
        print(f"Scraper: Error de Timeout durante el renderizado de JavaScript para {product_url}: {e}")
    except Exception as e:
        print(f"Scraper: Error al procesar {product_url}: {e}")
    finally:
        session.close()
    return product_data
