# main.py

import os
import copy
import json  # Para manejar el archivo de estado JSON
from datetime import datetime

import config
import scraper
import notifier
import price_history

STATE_FILE_PATH = "product_states.json"  # Nombre del archivo de estado

# Constantes INTERNAS de los tipos de oferta. Son los valores que se guardan en
# product_states.json, así que NO los cambies o el bot volverá a notificar todo
# una vez como "cambio de tipo de oferta".
OFFER_TYPE_OH = "Exclusivo oh! y oh! pay"
OFFER_TYPE_PROMO = "Precio Promocional"
OFFER_TYPE_REGULAR = "Precio regular"  # O "Sin Oferta"

# Etiquetas que se muestran en el correo y en el log (las de la tienda).
LABEL_REGULAR = "Precio regular"
LABEL_ONLINE = "Precio online"
LABEL_SIP = "Exclusivo con Sip"

# Colores de los precios en el correo (ambos se muestran en negrita).
COLOR_ONLINE = "#000000"  # negro
COLOR_SIP = "#00AEEF"     # celeste

DISPLAY_LABELS = {
    OFFER_TYPE_REGULAR: LABEL_REGULAR,
    OFFER_TYPE_PROMO: LABEL_ONLINE,
    OFFER_TYPE_OH: LABEL_SIP,
}


def display_label(offer_type):
    """Devuelve la etiqueta visible para un tipo de oferta interno.
    Si viene un valor desconocido, lo devuelve tal cual."""
    return DISPLAY_LABELS.get(offer_type, offer_type)


def load_product_states():
    """Carga los estados de los productos desde el archivo JSON."""
    if os.path.exists(STATE_FILE_PATH):
        try:
            with open(STATE_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"Main: Error al decodificar {STATE_FILE_PATH}. Se creará uno nuevo si es necesario.")
            return {}  # Devuelve vacío si el archivo está corrupto
    return {}


def save_product_states(states):
    """Guarda los estados de los productos en el archivo JSON."""
    try:
        with open(STATE_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(states, f, indent=4, ensure_ascii=False)
        print(f"Main: Estados de los productos guardados en {STATE_FILE_PATH}")
    except IOError as e:
        print(f"Main: Error al guardar estados en {STATE_FILE_PATH}: {e}")


def determine_best_offer(product_data):
    """Determina la mejor oferta actual y su tipo basado en la jerarquía."""
    oh_price = product_data.get("oh_price_value")
    promo_price = product_data.get("promo_price_value")
    regular_price = product_data.get("regular_price_value")  # Lo usamos para saber si hay algún precio

    if oh_price is not None:
        return OFFER_TYPE_OH, oh_price, product_data.get("oh_price_text")
    elif promo_price is not None and product_data.get("is_regular_price_striked"):  # Asegurar que el regular esté tachado para promo
        return OFFER_TYPE_PROMO, promo_price, product_data.get("promo_price_text")
    elif regular_price is not None:  # Si solo hay regular (o los otros no son válidos)
        return OFFER_TYPE_REGULAR, regular_price, product_data.get("regular_price_text")
    else:
        return None, None, None  # No se encontraron precios válidos


def build_price_details_html(product_data, best_offer_type):
    """Arma el bloque de precios del correo mostrando siempre las 3 etiquetas
    de la tienda, en el mismo orden que en la página. La mejor oferta se
    resalta en color y en negrita."""
    regular_text = product_data.get("regular_price_text") or "N/A"
    online_text = product_data.get("promo_price_text") or "N/A"
    sip_text = product_data.get("oh_price_text") or "N/A"

    # (etiqueta, texto del precio, tipo interno, color del precio)
    # El precio regular va sin estilo especial (color None).
    rows = [
        (LABEL_REGULAR, regular_text, OFFER_TYPE_REGULAR, None),
        (LABEL_ONLINE, online_text, OFFER_TYPE_PROMO, COLOR_ONLINE),
        (LABEL_SIP, sip_text, OFFER_TYPE_OH, COLOR_SIP),
    ]

    html = ""
    for label, text, offer_type, color in rows:
        if color:
            price_html = f'<strong><span style="color:{color};">{text}</span></strong>'
        else:
            price_html = text
        marker = " &larr; mejor oferta" if offer_type == best_offer_type and color else ""
        html += f"<p>{label}: {price_html}{marker}</p>\n"
    return html


def process_product(product_url, product_states):
    print(f"Main: Procesando URL: {product_url}")
    product_data = scraper.fetch_product_data(product_url, config.DEFAULT_HEADERS)

    if not product_data:
        print(f"Main: No se pudieron obtener datos para {product_url}.")
        return False  # Indica que no se pudo procesar para no intentar guardar estado inválido

    product_name_scraped = product_data.get('name', 'Producto Desconocido')
    print(f"Main: Producto detectado: {product_name_scraped}")

    current_best_offer_type, current_best_price, current_best_price_text = determine_best_offer(product_data)

    # Historial de precios: agrega un punto solo si el precio cambió.
    # Se guarda dentro de product_states.json (clave "__price_history__").
    if price_history.record_price(product_states, product_url,
                                  product_data.get("regular_price_value"), current_best_price):
        print(f"Main: Cambio de precio registrado en el historial (S/ {current_best_price}).")

    # Cargar estado anterior para esta URL
    last_state = product_states.get(product_url, {})
    last_notified_type = last_state.get("last_notified_type")
    last_notified_price = last_state.get("last_notified_price")

    send_notification = False
    notification_reason = ""

    if current_best_offer_type == OFFER_TYPE_REGULAR or current_best_offer_type is None:
        print(f"Main: Solo se encontró el precio regular ({current_best_price_text}) o ningún precio válido. No se enviará notificación.")
        # Actualizar estado si la última vez fue una oferta mejor y ahora es solo regular
        if last_notified_type and last_notified_type != OFFER_TYPE_REGULAR:
            product_states[product_url] = {
                "last_notified_type": OFFER_TYPE_REGULAR,  # O None si current_best_offer_type es None
                "last_notified_price": current_best_price,  # O None
                "last_checked_timestamp": datetime.now().isoformat()
            }
            print(f"Main: Estado actualizado para {product_url} a 'Solo Regular' o 'Sin Precio'.")
            return True  # Indica que el estado cambió y debe guardarse

    else:  # Tenemos una oferta (Sip o Online)
        current_label = display_label(current_best_offer_type)
        last_label = display_label(last_notified_type)

        if not last_notified_type or last_notified_type == OFFER_TYPE_REGULAR:
            send_notification = True
            notification_reason = f"Nueva oferta '{current_label}' detectada."
        elif current_best_offer_type != last_notified_type:
            send_notification = True
            notification_reason = f"Tipo de oferta cambió de '{last_label}' a '{current_label}'."
        elif current_best_offer_type == last_notified_type and current_best_price != last_notified_price:
            send_notification = True
            notification_reason = f"Precio de '{current_label}' cambió de S/ {last_notified_price} a S/ {current_best_price}."
        else:
            print(f"Main: La oferta '{current_label}' a S/ {current_best_price} es la misma que la última vez. No se notifica.")

    if send_notification:
        print(f"Main: {notification_reason} ¡Notificando!")

        sender_email = os.environ.get('SENDER_EMAIL')
        sender_app_password = os.environ.get('SENDER_APP_PASSWORD')
        recipient_email = os.environ.get('RECIPIENT_EMAIL')
        smtp_server = os.environ.get('SMTP_SERVER')
        smtp_port = os.environ.get('SMTP_PORT')

        current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        email_subject = f"¡Alerta de Promoción: {product_name_scraped}!"

        body_intro = f"<p>¡Se ha detectado una promoción para el producto <strong>{product_name_scraped}</strong>!</p>"
        body_url = f"<p><strong>URL del Producto:</strong> <a href=\"{product_data.get('url', '#')}\">{product_data.get('url', 'No disponible')}</a></p>"

        # Siempre se muestran las 3 etiquetas de la tienda
        body_price_details = build_price_details_html(product_data, current_best_offer_type)

        # Gráfico del historial (si falla o no hay matplotlib, el correo sale sin gráfico)
        inline_images = {}
        body_chart = ""
        chart_png = price_history.build_chart_png(product_states, product_url)
        if chart_png:
            inline_images["price_chart"] = chart_png
            body_chart = (
                '<p><img src="cid:price_chart" alt="Historial de precio" width="600" '
                'style="max-width:100%;height:auto;border:0;"></p>'
            )

        body_footer = f"<p><em>Razón de la notificación: {notification_reason}</em></p>"
        body_footer += f"<p><em>Revisado el: {current_time_str} (UTC)</em></p>"

        email_body_html = f"""
        <html>
        <body>
            {body_intro}
            {body_url}
            {body_price_details}
            {body_chart}
            {body_footer}
        </body>
        </html>
        """

        if notifier.send_email_notification(
            email_subject, email_body_html, recipient_email,
            sender_email, sender_app_password, smtp_server, smtp_port,
            inline_images=inline_images
        ):
            # Solo actualiza el estado guardado si la notificación fue exitosa
            product_states[product_url] = {
                "last_notified_type": current_best_offer_type,
                "last_notified_price": current_best_price,
                "last_checked_timestamp": datetime.now().isoformat()
            }
            return True  # Indica que el estado cambió y debe guardarse

    return False  # No se envió notificación o no cambió el estado de forma relevante


def run_checker():
    print("Main: Iniciando el comprobador de precios...")
    if not config.PRODUCT_URLS:
        print("Main: No hay URLs de productos configuradas en config.py. Saliendo.")
        return

    product_states = load_product_states()
    initial_states_copy = copy.deepcopy(product_states)  # Para comparar si hubo cambios (copia profunda: el historial es anidado)

    for url in config.PRODUCT_URLS:
        process_product(url, product_states)  # Pasa el dict de estados para que pueda ser modificado
        print("-" * 30)

    # Guardar estados solo si han cambiado
    if product_states != initial_states_copy:
        save_product_states(product_states)
    else:
        print("Main: No hubo cambios en los estados de los productos, no se reescribe el archivo de estado.")

    print("Main: Comprobador de precios finalizado.")


if __name__ == "__main__":
    run_checker()
