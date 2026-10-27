# price_history.py
#
# Guarda el historial de precios de cada producto y genera el gráfico que se
# incluye en el correo.
#
# El historial vive DENTRO de product_states.json, bajo la clave reservada
# "__price_history__". Así se guarda en el repo junto con el estado que ya
# persistes hoy, sin tener que cambiar el workflow para versionar otro archivo.
#
# Formato:
#   "__price_history__": {
#       "<url>": [
#           {"t": "2026-10-26T22:15:00", "best": 41.7, "regular": 54.9},
#           ...
#       ]
#   }
#
# Solo se agrega un punto cuando el precio cambia (mejor precio o precio regular).

import io
from datetime import datetime, timedelta

HISTORY_KEY = "__price_history__"
MAX_POINTS_PER_PRODUCT = 500

MESES = ["ene", "feb", "mar", "abr", "may", "jun",
         "jul", "ago", "sep", "oct", "nov", "dic"]

# Paleta (sencilla y limpia)
COLOR_LINE = "#00AEEF"     # celeste, el mismo de "Exclusivo con Sip"
COLOR_REGULAR = "#9CA3AF"  # gris para el precio regular
COLOR_GRID = "#E5E7EB"
COLOR_TEXT = "#6B7280"
COLOR_TITLE = "#374151"


def get_history(states, url):
    """Devuelve la lista de puntos (puede estar vacía) de un producto."""
    return states.get(HISTORY_KEY, {}).get(url, [])


def record_price(states, url, regular_price, best_price, now=None):
    """Agrega un punto al historial si el precio cambió respecto al último.
    Devuelve True si se agregó un punto nuevo."""
    if best_price is None:
        return False

    now = now or datetime.now()
    entries = states.setdefault(HISTORY_KEY, {}).setdefault(url, [])

    if entries:
        last = entries[-1]
        if last.get("best") == best_price and last.get("regular") == regular_price:
            return False

    entries.append({
        "t": now.isoformat(timespec="seconds"),
        "best": best_price,
        "regular": regular_price,
    })

    # Evita que el archivo crezca sin límite
    if len(entries) > MAX_POINTS_PER_PRODUCT:
        del entries[:len(entries) - MAX_POINTS_PER_PRODUCT]
    return True


def build_chart_png(states, url, now=None):
    """Genera el gráfico del historial de un producto y lo devuelve como bytes
    PNG. Devuelve None si no hay datos o si matplotlib no está instalado, para
    que un problema con el gráfico nunca impida enviar el correo."""
    entries = get_history(states, url)
    if not entries:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")  # sin pantalla (GitHub Actions)
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        from matplotlib.ticker import FuncFormatter, MaxNLocator
    except ImportError:
        print("PriceHistory: matplotlib no está instalado; el correo irá sin gráfico.")
        return None

    try:
        now = now or datetime.now()
        times = [datetime.fromisoformat(e["t"]) for e in entries]
        best = [float(e["best"]) for e in entries]
        regular = [float(e["regular"]) if e.get("regular") is not None else b
                   for e, b in zip(entries, best)]

        single_point = len(entries) < 2
        x_end = max(now, times[-1])

        # Rango del eje X
        if single_point:
            x_min = times[0] - timedelta(days=1)
            x_max = times[0] + timedelta(days=1)
        else:
            span = (x_end - times[0]) or timedelta(hours=1)
            x_min = times[0] - span * 0.03
            x_max = x_end + span * 0.08

        # Rango del eje Y (no parte de cero para que los cambios se aprecien)
        all_vals = best if single_point else best + regular
        lo, hi = min(all_vals), max(all_vals)
        spread = (hi - lo) or max(hi * 0.1, 1.0)
        y_min = lo - spread * 0.38
        y_max = hi + spread * 0.40

        fig, ax = plt.subplots(figsize=(6.4, 2.9), dpi=200)
        fig.patch.set_facecolor("white")
        ax.set_facecolor("white")

        # Precio regular (referencia)
        if not single_point:
            ax.step(times + [x_end], regular + [regular[-1]], where="post",
                    color=COLOR_REGULAR, linewidth=1.2, linestyle=(0, (4, 3)),
                    zorder=2, label="Precio regular")

        # Mejor precio: línea escalonada + área suave
        if not single_point:
            ax.step(times + [x_end], best + [best[-1]], where="post",
                    color=COLOR_LINE, linewidth=2.4, solid_capstyle="round",
                    zorder=4, label="Mejor precio")
            ax.fill_between(times + [x_end], best + [best[-1]], y_min, step="post",
                            color=COLOR_LINE, alpha=0.12, linewidth=0, zorder=1)

        # Un punto por cada cambio del mejor precio (los cambios que solo
        # afectan al precio regular se guardan pero no se marcan con punto)
        change_idx = [i for i in range(len(best)) if i == 0 or best[i] != best[i - 1]]
        ax.scatter([times[i] for i in change_idx], [best[i] for i in change_idx],
                   s=26, color="white", edgecolor=COLOR_LINE, linewidth=1.8, zorder=5)

        # Punto y etiqueta del precio actual (último cambio del mejor precio)
        last_i = change_idx[-1]
        cur = best[last_i]
        cur_x = times[last_i]
        ax.scatter([cur_x], [cur], s=60, color=COLOR_LINE, edgecolor="white",
                   linewidth=2, zorder=6)
        # Si el último cambio fue una bajada, la línea llega desde arriba: la
        # etiqueta va debajo del punto para que no la atraviese. Si fue una
        # subida (o no hay cambio previo), va arriba.
        dropped = last_i > 0 and best[last_i] < best[last_i - 1]
        ax.annotate(f"S/ {cur:.2f}", (cur_x, cur),
                    xytext=(0, -11 if dropped else 11), textcoords="offset points",
                    ha="center", va="top" if dropped else "bottom",
                    fontsize=10, fontweight="bold", color=COLOR_LINE, zorder=7)

        # Etiqueta del mínimo histórico si es distinto del actual
        min_val = min(best)
        if min_val < cur - 0.001:
            min_i = best.index(min_val)
            ax.annotate(f"mín. S/ {min_val:.2f}", (times[min_i], min_val),
                        xytext=(0, -9), textcoords="offset points",
                        ha="center", va="top", fontsize=8, color=COLOR_TEXT, zorder=7)

        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)

        # Estilo sencillo y limpio
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(COLOR_GRID)
        ax.grid(axis="y", color=COLOR_GRID, linestyle=(0, (3, 3)), linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(axis="both", length=0, labelsize=8, labelcolor=COLOR_TEXT, pad=6)

        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, steps=[1, 2, 2.5, 5, 10]))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"S/ {v:.0f}"))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=2, maxticks=5))

        def _fmt_date(x, _):
            d = mdates.num2date(x)
            return f"{d.day} {MESES[d.month - 1]}"
        ax.xaxis.set_major_formatter(FuncFormatter(_fmt_date))

        # Título y datos resumen
        fig.text(0.045, 0.955, "HISTORIAL DE PRECIO", fontsize=9, fontweight="bold",
                 color=COLOR_TITLE, ha="left", va="top")
        fig.text(0.955, 0.955, f"Mínimo S/ {min_val:.2f}  ·  Máximo S/ {max(best):.2f}",
                 fontsize=8, color=COLOR_TEXT, ha="right", va="top")

        if single_point:
            ax.text(0.5, 0.12, "Historial en construcción: se completará con cada cambio de precio",
                    transform=ax.transAxes, ha="center", va="center",
                    fontsize=8, color=COLOR_TEXT, style="italic")
        else:
            ax.legend(loc="upper left", fontsize=7.5, frameon=False, labelcolor=COLOR_TEXT,
                      ncol=2, handlelength=1.8, borderaxespad=0.1)

        fig.subplots_adjust(left=0.10, right=0.96, top=0.80, bottom=0.14)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor="white")
        plt.close(fig)
        return buf.getvalue()

    except Exception as e:  # el gráfico nunca debe romper el envío del correo
        print(f"PriceHistory: no se pudo generar el gráfico: {e}")
        return None
