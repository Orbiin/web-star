#!/usr/bin/env python3
"""SC HUB - recolector de comercio (UEX Corp API 2.0).

Uso:
  python collector/collect.py            -> genera site/data/commodities.json y meta.json
  python collector/collect.py --inspect  -> muestra los campos reales de la API

Solo usa la libreria estandar de Python 3.9+.
"""
import json, os, re, sys, time, urllib.request, urllib.error
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


def wiki_get(path, timeout=60, tries=2):
    last = None
    for _ in range(tries):
        try:
            req = urllib.request.Request("https://api.star-citizen.wiki/api/" + path, headers={
                "User-Agent": "sc-hub-collector/0.5 (web-star)", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception as e:
            last = e
            time.sleep(2)
    raise last


TAGS = {"is_cargo": "Carga", "is_military": "Militar", "is_mining": "Minería", "is_exploration": "Exploración",
        "is_salvage": "Salvamento", "is_medical": "Médica", "is_racing": "Carreras", "is_passenger": "Pasajeros",
        "is_starter": "Inicial", "is_stealth": "Sigilo", "is_refuel": "Repostaje", "is_repair": "Reparación",
        "is_science": "Ciencia", "is_industrial": "Industrial", "is_bomber": "Bombardero", "is_carrier": "Portanaves",
        "is_interdiction": "Interdicción", "is_construction": "Construcción", "is_ground_vehicle": "Terrestre"}


def normalize_ships(raw, where, now, wid_of=None, approx=()):
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
                    "photo": v.get("url_photo"), "store": v.get("url_store"), "wid": wid_of(v) if wid_of else None, "wid_approx": v.get("id") in approx, "buy": p["buy"], "rent": p["rent"]})
    out.sort(key=lambda x: ((x["maker"] or ""), (x["full"] or x["name"] or "")))
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "ships": out}


def shape(v, depth=3):
    """Resume la ESTRUCTURA de un JSON (claves y primeros elementos) para poder inspeccionarlo."""
    def sub(x):  # al agotar la profundidad solo se oculta lo anidado, los valores simples se ven
        return shape(x, depth - 1) if depth > 0 or not isinstance(x, (dict, list)) else "..."
    if isinstance(v, dict):
        return {k: sub(x) for k, x in list(v.items())[:40]}
    if isinstance(v, list):
        return [sub(x) for x in v[:2]] + (["(+%d mas)" % (len(v) - 2)] if len(v) > 2 else [])
    return v[:60] if isinstance(v, str) else v


KEEP = {"WeaponGun", "Turret", "TurretBase", "MissileLauncher", "Missile", "Shield", "PowerPlant", "Cooler",
        "QuantumDrive", "Radar", "MainThruster", "ManneuverThruster", "FuelTank", "QuantumFuelTank",
        "WeaponDefensive", "EMP", "QuantumInterdictionGenerator", "TractorBeam", "SalvageHead"}


SWAP = ["WeaponGun", "Shield", "PowerPlant", "Cooler", "QuantumDrive"]   # piezas que se pueden cambiar en la web
TURRET_BASES = {"TurretBase", "UtilityTurret"}                                 # armas aqui NO son del piloto


def item_stats(it, t):
    """Estadisticas utiles de una pieza (las mismas para la instalada y para el catalogo)."""
    em, rn = it.get("emission") or {}, it.get("resource_network") or {}
    d = {"uuid": it.get("uuid"), "name": it.get("name"), "size": it.get("size"), "grade": it.get("grade"),
         "class": it.get("class"), "maker": (it.get("manufacturer") or {}).get("name"),
         "em": em.get("em_max"), "ir": em.get("ir"), "dur": (it.get("durability") or {}).get("health"),
         "pwr": (((rn.get("usage") or {}).get("power")) or {}).get("max")}
    if t == "WeaponGun":
        v = it.get("vehicle_weapon") or {}
        dm, modes = v.get("damage") or {}, v.get("modes") or []
        kind, dd = v.get("type") or "", dm.get("dps") or {}
        low = kind.lower()
        top = max(("energy", "physical", "distortion"), key=lambda k: dd.get(k) or 0)
        d.update(dps=dm.get("burst") or (modes[0].get("damage_per_second") if modes else None),
                 sustained=dm.get("sustained_60s"), alpha=dm.get("alpha_total"), range=v.get("range"),
                 rpm=v.get("rpm"), kind=kind, speed=(v.get("ammunition") or {}).get("speed"),
                 spread=(v.get("spread") or {}).get("max"), ammo=v.get("capacity"),
                 wtype=next((n for k, n in (("repeater", "Repeater"), ("cannon", "Cannon"), ("gatling", "Gatling"),
                                            ("scattergun", "Scattergun")) if k in low), "Otra"),
                 dmg=({"energy": "energy", "physical": "ballistic", "distortion": "distortion"}[top]
                      if (dd.get(top) or 0) > 0 else None))
    elif t == "Shield":
        sh = it.get("shield") or {}
        pick3 = lambda m: [((m or {}).get(k) or {}).get("max") for k in ("physical", "energy", "distortion")]
        d.update(hp=sh.get("max_health"), regen=sh.get("regen_rate"), res=pick3(sh.get("resistance")),
                 ab=pick3(sh.get("absorption")))
    elif t == "PowerPlant":
        d["seg"] = (it.get("power_plant") or {}).get("power_segment_generation")
    elif t == "Cooler":
        d["seg"] = (it.get("cooler") or {}).get("coolant_segment_generation")
    elif t == "QuantumDrive":
        q = it.get("quantum_drive") or {}
        sj = q.get("standard_jump") or {}
        d.update(speed=sj.get("drive_speed"), spool=sj.get("spool_up_time"), cooldown=sj.get("cooldown_time"),
                 fuel_rate=q.get("fuel_rate"))
    return d


def best_price(it):
    rows = [p for p in ((it.get("uex_prices") or {}).get("purchase") or []) if (p.get("price_buy") or 0) > 0]
    if not rows:
        return None
    b = min(rows, key=lambda p: p["price_buy"])
    loc = b.get("starmap_location") or {}
    return {"price": b["price_buy"], "terminal": b.get("terminal_name"), "place": loc.get("name"),
            "system": loc.get("star_system_name"), "shops": len(rows)}


def flatten_ports(ports, out, seen, slots=None, under=()):
    """Recorre los puertos de una nave (y los hijos de cada uno): lista de componentes y huecos cambiables."""
    for p in ports or []:
        it = p.get("equipped_item") or {}
        t = it.get("type") or p.get("type")
        seen[t] = seen.get(t, 0) + 1
        real = it and "PLACEHOLDER" not in (it.get("name") or "").upper()
        if real and t in KEEP:
            sz = p.get("sizes") or {}
            out.append({"type": t, "label": it.get("type_label"), "name": it.get("name"), "size": it.get("size"),
                        "grade": it.get("grade"), "class": it.get("class"), "min": sz.get("min"), "max": sz.get("max"),
                        "maker": (it.get("manufacturer") or {}).get("name")})
        if slots is not None and real and t in SWAP and p.get("editable") is not False:
            sz = p.get("sizes") or {}
            slots.append({"port": p.get("name"), "type": t, "min": sz.get("min"), "max": sz.get("max"),
                          "turret": any(x in TURRET_BASES for x in under), "cur": item_stats(it, t)})
        flatten_ports(p.get("ports"), out, seen, slots, (under + (t,)) if t else under)


def compact_ship(w, seen):
    """Se queda con lo que usa la web de una nave de la wiki (la ficha original pesa mucho)."""
    flat, grouped, slots = [], {}, []
    flatten_ports(w.get("ports"), flat, seen, slots)
    for x in flat:
        if x["type"] in SWAP:
            continue            # estas se cambian desde los huecos (slots), no salen en la tabla fija
        k = (x["type"], x["name"], x["size"], x["grade"], x["maker"])
        if k in grouped:
            grouped[k]["n"] += 1
        else:
            grouped[k] = dict(x, n=1)
    sh, wp, ar = w.get("shield") or {}, w.get("weaponry") or {}, w.get("armor") or {}
    sig = w.get("signature") or {}
    img = ((w.get("images") or [{}])[0] or {}).get("thumbnail_url")
    return {"uuid": w.get("uuid"), "name": w.get("name"), "game_name": w.get("game_name"),
            "maker": (w.get("manufacturer") or {}).get("name"), "role": w.get("role"), "career": w.get("career"),
            "size_class": w.get("size_class"), "msrp": w.get("msrp"), "crew": w.get("crew"),
            "cargo": w.get("cargo_capacity"), "mass": w.get("mass_total"), "dim": w.get("dimension"),
            "health": w.get("health"),
            "shield": {"hp": sh.get("hp"), "regen": sh.get("regeneration"), "face": sh.get("face_type"),
                       "resist": sh.get("resistance"), "absorb": sh.get("absorption")},
            "weapons": {"dps": wp.get("pilot_dps"), "sustained": wp.get("pilot_sustained_dps"),
                        "alpha": wp.get("pilot_alpha"), "fixed": (wp.get("fixed_weapons") or {}).get("weapons"),
                        "missiles": wp.get("missiles"), "snapshot": w.get("weapon_snapshot")},
            "speed": w.get("speed"), "agility": w.get("agility"), "fuel": (w.get("fuel") or {}).get("capacity"),
            "quantum": w.get("quantum"), "armor": {"health": ar.get("health"), "damage": ar.get("damage_multiplier")},
            "signature": {k: sig.get(k) for k in ("ir_shields", "em_shields", "ir_quantum", "em_quantum")},
            "insurance": w.get("insurance"), "image": img, "ports": list(grouped.values()), "slots": slots,
            "power_gen": (w.get("power") or {}).get("generation_segments"),
            "cool_gen": (w.get("cooling") or {}).get("generation_segments")}


def wiki_ships():
    """Descarga todas las naves de la wiki (11 paginas aprox.). Devuelve ({uuid: ficha}, diagnostico)."""
    by_uuid, seen, page, last, failed = {}, {}, 1, 1, 0
    while page <= min(last, 25):
        try:
            body = wiki_get("vehicles?page%5Bnumber%5D=" + str(page))
            last = (body.get("meta") or {}).get("last_page", 1)
            for w in body.get("data", []):
                if w.get("uuid"):
                    by_uuid[w["uuid"]] = compact_ship(w, seen)
        except Exception as e:
            failed += 1
            print(f"Wiki pagina {page} fallida: {e}", file=sys.stderr)
        page += 1
        time.sleep(1)
    if not by_uuid or failed > max(2, last // 2):
        raise RuntimeError(f"Wiki incompleta: {len(by_uuid)} naves, {failed} paginas fallidas")
    top = dict(sorted(seen.items(), key=lambda kv: -kv[1])[:40])
    return by_uuid, {"wiki_ships": len(by_uuid), "pages_failed": failed, "port_types": top}


def wiki_items():
    """Catalogo de piezas intercambiables (unas 460) con sus estadisticas y su precio mas barato."""
    cat, diag = {}, {}
    for t in SWAP:
        rows, page, last, failed = [], 1, 1, 0
        while page <= min(last, 12):
            try:
                body = wiki_get("items?filter%5Btype%5D=" + t + "&page%5Bnumber%5D=" + str(page))
                last = (body.get("meta") or {}).get("last_page", 1)
                for it in body.get("data", []):
                    if "PLACEHOLDER" in (it.get("name") or "").upper() or not it.get("uuid"):
                        continue
                    row = item_stats(it, t)
                    row["price"] = best_price(it)
                    rows.append(row)
            except Exception as e:
                failed += 1
                print(f"Wiki items {t} pagina {page} fallida: {e}", file=sys.stderr)
            page += 1
            time.sleep(1)
        cat[t] = rows
        diag[t] = {"items": len(rows), "pages_failed": failed}
    if not any(cat.values()):
        raise RuntimeError("Catalogo de piezas vacio")
    return cat, diag


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


EDITION = re.compile(r"\s+(best in show edition|solstice edition|carbon edition|talus edition|liberator edition|"
                     r"phoenix emerald|\w+ bay pod|general research pod)$", re.I)


def match_ships(vehicles, by_uuid):
    """Une cada nave de UEX con su ficha de la wiki: primero por uuid, si no por nombre."""
    by_name = {norm(w.get("game_name") or w.get("name")): u for u, w in by_uuid.items()}
    matched, approx, n_uuid, n_name, missing = {}, set(), 0, 0, []
    for v in vehicles:
        base = norm(EDITION.sub("", v.get("name_full") or ""))
        if v.get("uuid") in by_uuid:
            matched[v["id"]], n_uuid = v["uuid"], n_uuid + 1
        elif norm(v.get("name_full")) in by_name:
            matched[v["id"]], n_name = by_name[norm(v.get("name_full"))], n_name + 1
        elif base in by_name:                       # edicion especial: se usan las cifras del modelo base
            matched[v["id"]] = by_name[base]
            approx.add(v["id"])
        else:
            missing.append(v.get("name_full") or v.get("name"))
    return matched, approx, {"matched_by_uuid": n_uuid, "matched_by_name": n_name, "matched_as_base": len(approx),
                             "unmatched": missing[:40], "unmatched_total": len(missing)}


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
    out, skipped, no_header = [], [], []
    for f in sorted(d.iterdir()) if d.is_dir() else []:
        if f.suffix.lower() != ".md" or f.name.startswith("_"):
            skipped.append(f.name)
            continue
        # utf-8-sig quita la marca BOM que algunos editores añaden y que rompe la cabecera
        text, meta, body = f.read_text(encoding="utf-8-sig", errors="replace"), {}, ""
        parts = text.split("---", 2) if text.lstrip().startswith("---") else None
        if parts and len(parts) == 3:
            for line in parts[1].splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip().lower()] = v.strip()
            body = parts[2].strip()
        else:
            body = text.strip()
            no_header.append(f.name)
        out.append({"slug": f.stem, "title": meta.get("titulo", f.stem),
                    "status": meta.get("estado", "inactivo").lower(), "patch": meta.get("parche", ""),
                    "summary": meta.get("resumen", ""), "requirements": meta.get("requisitos", ""),
                    "players": meta.get("jugadores", ""), "rewards": meta.get("recompensas", ""),
                    "image": meta.get("imagen", ""), "body": body})
    out.sort(key=lambda g: (g["status"] != "activo", g["title"].lower()))
    return out, {"found": len(out), "skipped_files": skipped, "without_header": no_header}


PREV = os.environ.get("PREV_BASE", "").rstrip("/")       # donde esta publicada la web (datos de la vez anterior)
FORCE = os.environ.get("FORCE_STATIC", "").lower() in ("1", "true", "yes")
REFRESH_EVERY_H = 6      # tras un parche nuevo, se vuelven a pedir los datos fijos cada 6 h...
REFRESH_WINDOW_D = 3     # ...durante 3 dias, porque UEX tarda en actualizar los suyos


def prev_json(name):
    """Descarga un archivo de la web ya publicada (None si no existe todavia)."""
    if not PREV:
        return None
    try:
        req = urllib.request.Request(f"{PREV}/{name}?t={int(time.time())}",
                                     headers={"User-Agent": "sc-hub-collector/0.4"})
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except Exception:
        return None


def static_data(live, now, where):
    """Naves (UEX) y sus fichas (wiki): solo cambian con un parche, asi que solo se piden cuando toca.
    Devuelve (ships_json, detalle_json, catalogo_json, state, razon). Si no toca refrescar, reutiliza lo ya publicado."""
    state, old_ships, old_detail, old_items = (prev_json("static.json"), prev_json("ships.json"),
                                                prev_json("shipdata.json"), prev_json("items.json"))
    changed = bool(live) and (not state or state.get("version") != live)
    first_seen = now if (changed or not state) else state.get("first_seen", now)
    last = (state or {}).get("fetched_at", 0)
    window = now - first_seen < REFRESH_WINDOW_D * 86400 and now - last > REFRESH_EVERY_H * 3600
    reason = ("forzado a mano" if FORCE else "sin datos previos" if not (state and old_ships and old_detail and old_items) else
              "parche nuevo" if changed else "refresco tras parche" if window else None)
    if reason:
        try:
            raw, diag = ships(where)
            try:
                by_uuid, wdiag = wiki_ships()
            except Exception as e:                       # sin la wiki, las naves siguen funcionando con UEX
                print("Wiki no disponible:", e, file=sys.stderr)
                by_uuid, wdiag = {}, {"error": str(e)}
            matched, approx, mdiag = match_ships(raw["vehicles"], by_uuid)
            diag["wiki"] = dict(wdiag, **mdiag)
            try:
                catalog, idiag = wiki_items() if by_uuid else ({}, {"error": "sin wiki"})
            except Exception as e:
                print("Catalogo de piezas no disponible:", e, file=sys.stderr)
                catalog, idiag = {}, {"error": str(e)}
            diag["items"] = idiag
            ship_data = normalize_ships(raw, where, now, lambda v: matched.get(v.get("id")), approx)
            detail = {"generated_at": iso(now), "ships": {u: by_uuid[u] for u in set(matched.values())}}
            items = {"generated_at": iso(now), "types": catalog}
            return ship_data, detail, items, {"version": live, "first_seen": first_seen, "fetched_at": now, "diag": diag}, reason
        except (SystemExit, Exception) as e:
            print("No se pudieron refrescar las naves:", e, file=sys.stderr)
            if not (state and old_ships and old_detail):
                return {"generated_at": None, "ships": []}, {"ships": {}}, {"types": {}}, {"version": live}, "error"
    return old_ships, old_detail, old_items, state, None


def main():
    if "--inspect" in sys.argv:
        data = fetch("commodities_prices_all")
        print(f"{len(data)} registros. Primer registro:")
        print(json.dumps(data[0], indent=2, ensure_ascii=False))
        return
    now = time.time()
    try:
        gv = fetch("game_versions", tries=1)
    except SystemExit:
        gv = None
    live = gv.get("live") if isinstance(gv, dict) else None
    try:                                                       # terminales: se piden siempre, como el comercio
        where = locate(fetch("terminals", tries=2))
    except SystemExit:
        where = {}
    ship_data, detail, items, state, reason = static_data(live, now, where)   # naves: solo con parche nuevo
    data, meta = build(fetch("commodities_prices_all"), where)         # precios: SIEMPRE
    meta["game_versions"] = gv
    meta["static_diag"] = {"version": live, "ships_refreshed": reason, "fetched_at": iso(state.get("fetched_at")),
                           "first_seen": iso(state.get("first_seen"))}
    if state.get("diag"):
        meta["ships_diag"] = state["diag"]      # se conserva entre ejecuciones (esta en static.json)
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        found, meta["guides_diag"] = guides()
        (OUT / "guides.json").write_text(json.dumps(found, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print("Guias no generadas:", e, file=sys.stderr)
    for name, obj in (("commodities.json", data), ("meta.json", meta), ("ships.json", ship_data),
                      ("shipdata.json", detail), ("items.json", items), ("static.json", state)):
        tmp = OUT / (name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUT / name)
    print(f"OK: {meta['commodities']} mercancias, {meta['price_records']} precios. Naves: {reason or 'reutilizadas'}.")


if __name__ == "__main__":
    main()
