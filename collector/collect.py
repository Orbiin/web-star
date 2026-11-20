#!/usr/bin/env python3
"""SC HUB - recolector de comercio (UEX Corp API 2.0).

Uso:
  python collector/collect.py            -> genera site/data/commodities.json y meta.json
  python collector/collect.py --inspect  -> muestra los campos reales de la API

Solo usa la libreria estandar de Python 3.9+.
"""
import json, os, sys, time, urllib.request, urllib.error
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

BASE = os.environ.get("UEX_BASE", "https://api.uexcorp.space/2.0")
TOKEN = os.environ.get("UEX_TOKEN", "")
OUT = Path(os.environ.get("OUT_DIR", "site/data"))

# Campos de commodities_prices_all (confirmados con datos reales el 2026-11-20).
# Confirmado con datos reales: scu_sell = demanda actual (0 = el terminal no compra ahora).
F = {
    "commodity": "commodity_name", "terminal": "terminal_name", "terminal_id": "id_terminal",
    "price_buy": "price_buy", "price_sell": "price_sell",
    "stock_buy": "scu_buy",           # SCU disponibles para comprar
    "demand_sell": "scu_sell",        # demanda actual al vender
    "stock_sell": "scu_sell_stock",   # SIN CONFIRMAR: stock del terminal al vender
    "avg_sell": "scu_sell_avg",
    "status_buy": "status_buy", "status_sell": "status_sell",
    "updated": "date_modified",
}


def fetch(endpoint, tries=3):
    req = urllib.request.Request(f"{BASE}/{endpoint}", headers={
        "User-Agent": "sc-hub-collector/0.2", "Accept": "application/json",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})})
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.load(r)
            if body.get("status", "ok") != "ok":
                raise RuntimeError(f"estado de la API: {body.get('status')}")
            return body["data"]
        except (urllib.error.URLError, RuntimeError, KeyError, ValueError) as e:
            print(f"[{endpoint}] intento {i + 1}/{tries} fallido: {e}", file=sys.stderr)
            time.sleep(2 ** (i + 1))
    raise SystemExit(f"No se pudo obtener {endpoint}")


def g(rec, key):
    return rec.get(F[key])


def iso(ts):
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
    return None


def positive_or_unknown(v):
    return v is None or v > 0


def locate(terminals):
    """id_terminal -> sistema y lugar. Campos de /terminals sin confirmar: se prueban varios."""
    out = {}
    for t in terminals or []:
        place = t.get("displayname") or next((t[k] for k in (
            "city_name", "space_station_name", "outpost_name", "orbit_name", "planet_name")
            if t.get(k)), None)
        out[t.get("id")] = {"system": t.get("star_system_name"), "place": place,
                            "live": t.get("is_available_live") != 0,
                            "max_container": t.get("max_container_size")}
    return out


def build(prices, where):
    if len(prices) < 50:
        raise SystemExit(f"Solo {len(prices)} precios: demasiado pocos, no se publica nada.")
    missing = [k for k in ("commodity", "terminal", "price_buy", "price_sell")
               if F[k] not in prices[0]]
    if missing:
        raise SystemExit(f"Faltan campos {missing}. Campos reales: {sorted(prices[0])}")
    now = time.time()

    def entry(p, side):
        loc = where.get(g(p, "terminal_id"), {})
        ts = g(p, "updated")
        e = {"terminal": g(p, "terminal"), "system": loc.get("system"), "place": loc.get("place"),
             "price": g(p, "price_" + side), "updated": iso(ts),
             "age_h": round((now - ts) / 3600, 1) if isinstance(ts, (int, float)) else None}
        if side == "buy":
            e["scu"] = g(p, "stock_buy")
            e["status"] = g(p, "status_buy")
        else:
            e["demand"] = g(p, "demand_sell")
            e["status"] = g(p, "status_sell")
        e["max_container"] = loc.get("max_container")
        return e

    by = {}
    for p in prices:
        if not where.get(g(p, "terminal_id"), {}).get("live", True):
            continue
        by.setdefault(g(p, "commodity"), []).append(p)

    items = []
    for name, recs in by.items():
        buys = [entry(p, "buy") for p in recs
                if (g(p, "price_buy") or 0) > 0 and positive_or_unknown(g(p, "stock_buy"))]
        # Se guardan TODAS las opciones utilizables (no solo las 5 mejores): la web
        # filtra por antigudad y sistema, y los datos frescos no suelen ser los mejores.
        sells = [entry(p, "sell") for p in recs
                 if (g(p, "price_sell") or 0) > 0 and positive_or_unknown(g(p, "demand_sell"))]
        buys.sort(key=lambda e: e["price"])
        sells.sort(key=lambda e: -e["price"])
        margin = None
        if buys and sells and buys[0]["terminal"] != sells[0]["terminal"]:
            margin = round(sells[0]["price"] - buys[0]["price"], 2)
        items.append({"name": name, "buy": buys, "sell": sells, "margin_per_scu": margin})
    items.sort(key=lambda i: -(i["margin_per_scu"] if i["margin_per_scu"] is not None else -1e18))

    buy_rows = [p for p in prices if (g(p, "price_buy") or 0) > 0]
    sell_rows = [p for p in prices if (g(p, "price_sell") or 0) > 0]
    stamps = [g(p, "updated") for p in prices if isinstance(g(p, "updated"), (int, float))]
    first = lambda rows, f: next((r for r in rows if f(r)), None)
    diag = {
        "buy_rows": len(buy_rows),
        "buy_rows_with_stock": sum((g(p, "stock_buy") or 0) > 0 for p in buy_rows),
        "sell_rows": len(sell_rows),
        "sell_rows_with_demand": sum((g(p, "demand_sell") or 0) > 0 for p in sell_rows),
        "sell_rows_with_stock": sum((g(p, "stock_sell") or 0) > 0 for p in sell_rows),
        "status_buy": dict(Counter(g(p, "status_buy") for p in buy_rows)),
        "status_sell": dict(Counter(g(p, "status_sell") for p in sell_rows)),
        "terminals_located": len(where),
    }
    meta = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "UEX Corp API 2.0", "price_records": len(prices),
            "commodities": len(items),
            "fresh_under_1h_percent": round(100 * sum(now - t < 3600 for t in stamps) / len(stamps)) if stamps else None,
            "fresh_under_24h_percent": round(100 * sum(now - t < 86400 for t in stamps) / len(stamps)) if stamps else None,
            "diag": diag}
    return {"generated_at": meta["generated_at"], "commodities": items}, meta


def first_ok(names):
    """Prueba varios nombres de endpoint (sin confirmar) y devuelve el primero que responde."""
    for n in names:
        try:
            return n, fetch(n, tries=1)
        except SystemExit:
            continue
    return None, None


def wiki_get(path):
    req = urllib.request.Request("https://api.star-citizen.wiki/api/" + path, headers={
        "User-Agent": "sc-hub-collector/0.3 (web-star)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def trim(v):
    """Recorta listas largas a 2 elementos para que el diagnostico sea corto."""
    if isinstance(v, dict):
        return {k: trim(x) for k, x in v.items()}
    if isinstance(v, list):
        return [trim(x) for x in v[:2]]
    return v


def wiki_diag():
    """Diagnostico de la API de la wiki (specs, componentes y posibles precios de UEX por nave)."""
    res = {}
    for label, path in (("list", "vehicles"), ("carrack", "vehicles/carrack?include=ports,components")):
        try:
            body = wiki_get(path)
            d = body.get("data", body)
            item = d[0] if isinstance(d, list) and d else d
            item = item if isinstance(item, dict) else {}
            res[label] = {"is_list": isinstance(d, list), "top_keys": sorted(body),
                          "meta": trim(body.get("meta")), "keys": sorted(item),
                          "uex_prices": trim(item.get("uex_prices")),
                          "basic": {k: item.get(k) for k in ("name", "msrp", "size_class", "role", "career")}}
        except Exception as e:
            res[label] = {"error": str(e)}
    return res


TAGS = {"is_cargo": "Carga", "is_military": "Militar", "is_mining": "Minería", "is_exploration": "Exploración",
        "is_salvage": "Salvamento", "is_medical": "Médica", "is_racing": "Carreras", "is_passenger": "Pasajeros",
        "is_starter": "Inicial", "is_stealth": "Sigilo", "is_refuel": "Repostaje", "is_repair": "Reparación",
        "is_science": "Ciencia", "is_industrial": "Industrial", "is_bomber": "Bombardero", "is_carrier": "Portanaves",
        "is_interdiction": "Interdicción", "is_construction": "Construcción", "is_ground_vehicle": "Terrestre"}


def normalize_ships(raw, where, now):
    """Une naves + precios de compra/alquiler + ubicacion del terminal."""
    prices = {}

    def add(kind, rows, field):
        for r in rows:
            price = r.get(field)
            loc = where.get(r.get("id_terminal"), {})
            if not price or price <= 0 or not loc.get("live", True):
                continue
            ts = r.get("date_modified")
            prices.setdefault(r.get("id_vehicle"), {"buy": [], "rent": []})[kind].append({
                "terminal": r.get("terminal_name"), "system": loc.get("system"), "place": loc.get("place"),
                "price": price, "age_h": round((now - ts) / 3600, 1) if isinstance(ts, (int, float)) else None})

    add("buy", raw["purchases"], "price_buy")
    add("rent", raw["rentals"], "price_rent")
    out = []
    for v in raw["vehicles"]:
        p = prices.get(v.get("id"), {"buy": [], "rent": []})
        for k in p:
            p[k].sort(key=lambda e: e["price"])
        out.append({"id": v.get("id"), "name": v.get("name"), "full": v.get("name_full"),
                    "maker": v.get("company_name"), "scu": v.get("scu"), "crew": v.get("crew"),
                    "mass": v.get("mass"), "size": [v.get("length"), v.get("width"), v.get("height")],
                    "pad": v.get("pad_type"), "tags": [t for f, t in TAGS.items() if v.get(f)],
                    "photo": v.get("url_photo"), "store": v.get("url_store"), "buy": p["buy"], "rent": p["rent"]})
    out.sort(key=lambda x: ((x["maker"] or ""), (x["full"] or x["name"] or "")))
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ships": out}


def ships(where):
    """Naves: lista, precios de compra y de alquiler. Se guardan los registros SIN transformar
    (los nombres de campo aun no estan confirmados) y se deja un diagnostico en meta.json."""
    spec = {"vehicles": ["vehicles"],
            "purchases": ["vehicles_purchases_prices_all", "vehicles_purchases_prices"],
            "rentals": ["vehicles_rentals_prices_all", "vehicles_rentals_prices"]}
    out, diag = {}, {"endpoints": {}, "counts": {}}
    for key, names in spec.items():
        name, data = first_ok(names)
        out[key] = data if isinstance(data, list) else []
        diag["endpoints"][key] = name
        diag["counts"][key] = len(out[key])
    return out, diag


def guides():
    """Lee web/guias/*.md (cabecera 'clave: valor' entre ---). Los que empiezan por _ se ignoran."""
    d = Path("web/guias")
    out = []
    for f in sorted(d.glob("*.md")) if d.is_dir() else []:
        if f.name.startswith("_"):
            continue
        text, meta, body = f.read_text(encoding="utf-8"), {}, ""
        parts = text.split("---", 2) if text.lstrip().startswith("---") else None
        if parts and len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            body = parts[2].strip()
        else:
            body = text.strip()
        out.append({"slug": f.stem, "title": meta.get("titulo", f.stem),
                    "status": meta.get("estado", "inactivo").lower(), "patch": meta.get("parche", ""),
                    "summary": meta.get("resumen", ""), "requirements": meta.get("requisitos", ""),
                    "players": meta.get("jugadores", ""), "rewards": meta.get("recompensas", ""),
                    "image": meta.get("imagen", ""), "body": body})
    out.sort(key=lambda g: (g["status"] != "activo", g["title"].lower()))
    return out


def main():
    if "--inspect" in sys.argv:
        data = fetch("commodities_prices_all")
        print(f"{len(data)} registros. Primer registro:")
        print(json.dumps(data[0], indent=2, ensure_ascii=False))
        return
    prices = fetch("commodities_prices_all")
    try:
        terminals = fetch("terminals", tries=2)
    except SystemExit:
        terminals = []
    data, meta = build(prices, locate(terminals))
    try:
        meta["game_versions"] = fetch("game_versions", tries=1)
    except SystemExit:
        meta["game_versions"] = None
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        (OUT / "guides.json").write_text(json.dumps(guides(), ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("Guias no generadas:", e, file=sys.stderr)
    try:
        where = locate(terminals)
        raw, meta["ships_diag"] = ships(where)
        ship_data = normalize_ships(raw, where, time.time())
        (OUT / "ships.json").write_text(json.dumps(ship_data, ensure_ascii=False), encoding="utf-8")
    except Exception as e:  # las naves nunca deben impedir publicar el comercio
        meta["ships_diag"] = {"error": str(e)}
    for name, obj in (("commodities.json", data), ("meta.json", meta)):
        tmp = OUT / (name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUT / name)
    print(f"OK: {meta['commodities']} mercancias, {meta['price_records']} precios.")


if __name__ == "__main__":
    main()
