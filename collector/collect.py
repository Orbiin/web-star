#!/usr/bin/env python3
"""SC HUB - recolector de comercio (UEX Corp API 2.0).

Uso:
  python collector/collect.py            -> genera site/data/commodities.json y meta.json
  python collector/collect.py --inspect  -> muestra los campos reales de la API (usar la 1ª vez)

Solo usa la libreria estandar de Python 3.9+.
"""
import json, os, sys, time, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path

BASE = os.environ.get("UEX_BASE", "https://api.uexcorp.space/2.0")
TOKEN = os.environ.get("UEX_TOKEN", "")          # opcional
OUT = Path(os.environ.get("OUT_DIR", "site/data"))
TOP = 5                                          # mejores terminales a guardar por mercancia

# Nombres de campo de la API. Se prueban en orden. Los marcados CONFIRMAR no los he
# podido comprobar contra la API real: ejecuta --inspect y ajusta si hace falta.
FIELDS = {
    "commodity_name": ["commodity_name"],
    "terminal_name":  ["terminal_name"],
    "system":         ["star_system_name"],
    "price_buy":      ["price_buy"],
    "price_sell":     ["price_sell"],
    "stock_buy":      ["scu_buy"],               # CONFIRMAR: SCU disponibles para comprar
    "demand_sell":    ["scu_sell"],              # CONFIRMAR: SCU que admite el terminal al vender
    "updated":        ["date_modified"],
}
REQUIRED = ["commodity_name", "terminal_name", "price_buy", "price_sell"]


def fetch(endpoint, tries=3):
    req = urllib.request.Request(f"{BASE}/{endpoint}", headers={
        "User-Agent": "sc-hub-collector/0.1", "Accept": "application/json",
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


def pick(rec, key):
    for name in FIELDS[key]:
        if rec.get(name) is not None:
            return rec[name]
    return None


def iso(ts):
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")
    return ts if isinstance(ts, str) else None


def inspect():
    data = fetch("commodities_prices_all")
    print(f"{len(data)} registros. Campos del primer registro:")
    print(json.dumps(data[0], indent=2, ensure_ascii=False))


def build(prices):
    if len(prices) < 50:
        raise SystemExit(f"Solo {len(prices)} precios: demasiado pocos, no se publica nada.")
    sample = prices[0]
    missing = [k for k in REQUIRED if pick(sample, k) is None]
    if missing:
        raise SystemExit(f"Faltan campos obligatorios {missing}. Campos reales: {sorted(sample)}")
    optional = {k: any(pick(p, k) is not None for p in prices[:200])
                for k in ("stock_buy", "demand_sell", "updated", "system")}

    by = {}
    for p in prices:
        by.setdefault(pick(p, "commodity_name"), []).append(p)

    def entry(p, price_key, scu_key):
        scu = pick(p, scu_key)
        return {"terminal": pick(p, "terminal_name"), "system": pick(p, "system"),
                "price": pick(p, price_key), "scu": scu, "updated": iso(pick(p, "updated"))}

    items = []
    for name, recs in by.items():
        buys = [entry(p, "price_buy", "stock_buy") for p in recs
                if (pick(p, "price_buy") or 0) > 0 and (pick(p, "stock_buy") or 1) > 0]
        sells = [entry(p, "price_sell", "demand_sell") for p in recs
                 if (pick(p, "price_sell") or 0) > 0 and (pick(p, "demand_sell") or 1) > 0]
        buys.sort(key=lambda e: e["price"])
        sells.sort(key=lambda e: -e["price"])
        buys, sells = buys[:TOP], sells[:TOP]
        margin = None
        if buys and sells and buys[0]["terminal"] != sells[0]["terminal"]:
            margin = round(sells[0]["price"] - buys[0]["price"], 2)
        items.append({"name": name, "buy": buys, "sell": sells, "margin_per_scu": margin})
    items.sort(key=lambda i: -(i["margin_per_scu"] or -1e18))

    now = time.time()
    stamps = [pick(p, "updated") for p in prices if isinstance(pick(p, "updated"), (int, float))]
    fresh = round(100 * sum(now - t < 3600 for t in stamps) / len(stamps)) if stamps else None
    meta = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "UEX Corp API 2.0", "price_records": len(prices),
            "commodities": len(items), "fields_available": optional,
            "fresh_under_1h_percent": fresh}
    return {"generated_at": meta["generated_at"], "commodities": items}, meta


def main():
    if "--inspect" in sys.argv:
        return inspect()
        prices = fetch("commodities_prices_all")
    data, meta = build(prices)
    try:
        meta["game_versions"] = fetch("game_versions", tries=1)
    except SystemExit:
        meta["game_versions"] = None
    OUT.mkdir(parents=True, exist_ok=True)
      (OUT / "sample.json").write_text(json.dumps(prices[:3], ensure_ascii=False, indent=1), encoding="utf-8")
    for name, obj in (("commodities.json", data), ("meta.json", meta)):
        tmp = OUT / (name + ".tmp")
        tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        tmp.replace(OUT / name)
    print(f"OK: {meta['commodities']} mercancias, {meta['price_records']} precios.")


if __name__ == "__main__":
    main()
