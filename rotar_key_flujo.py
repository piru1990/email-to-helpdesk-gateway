"""Rota la API key de Odoo que usa el flujo "Mesa de Ayuda → Odoo" sin mostrar ninguna key.

Pasos previos (el supervisor): en Odoo, Mi perfil → Seguridad de la cuenta → Nueva clave de API, y copiarla al
portapapeles. Luego:

  python rotar_key_flujo.py --clipboard           prueba la key, respalda el flujo (enmascarado) y la aplica
  python rotar_key_flujo.py --clipboard --dry-run solo prueba la key y dice qué cambiaría

Qué hace: toma la key nueva del portapapeles (y lo limpia), la prueba contra Odoo, exporta un respaldo del
flujo con la key enmascarada, cambia el valor de la variable OdooApiKey, actualiza el flujo, enmascara la
key vieja en los respaldos de respaldos\\ y dice si la key vieja es la misma que usa el MCP odoo-helpdesk
(en ese caso, antes de revocarla en Odoo, guarde una key propia para el MCP: _odoo_env.py set).
Después de la siguiente ejecución correcta del flujo (list_runs), revoque la key vieja en Odoo.
"""
import argparse
import datetime as dt
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
FLUJO = "<FLOW_ID>"
ACCION = "Odoo_API_key"
VARIABLE = "OdooApiKey"
MASCARA = "***CLAVE-REVOCADA***"

sys.path.insert(0, str(RAIZ))
import _odoo_env  # noqa: E402


def cargar_pa():
    """El server.py del MCP power-automate (nombre propio para no chocar con el de odoo-helpdesk)."""
    spec = importlib.util.spec_from_file_location("pa_server", AQUI / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def del_portapapeles():
    import win32clipboard
    win32clipboard.OpenClipboard()
    try:
        key = str(win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)).strip()
        win32clipboard.EmptyClipboard()
    finally:
        win32clipboard.CloseClipboard()
    return key


def probar(url, key):
    """Prueba la key contra Odoo (JSON-2). Devuelve el uid."""
    req = urllib.request.Request(
        f"{url.rstrip('/')}/json/2/res.users/context_get", data=b"{}", method="POST",
        headers={"Authorization": f"bearer {key}", "Content-Type": "application/json", "User-Agent": "odoo-mcp"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")).get("uid")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--clipboard", action="store_true", required=True, help="toma la key nueva del portapapeles")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    nueva = del_portapapeles()
    if len(nueva) < 20 or " " in nueva:
        raise SystemExit("Lo que hay en el portapapeles no parece una API key; no se cambió nada.")
    url, key_mcp = _odoo_env.credentials()
    uid = probar(url, nueva)
    print(f"Key nueva válida en Odoo (uid {uid}).")

    pa = cargar_pa()
    flujo = pa.get_flow(FLUJO)
    props = flujo["properties"]
    definicion = props["definition"]
    var = definicion["actions"][ACCION]["inputs"]["variables"][0]
    if var["name"] != VARIABLE:
        raise SystemExit(f"La acción {ACCION} no inicializa {VARIABLE}; no se cambió nada.")
    vieja = var["value"]
    if vieja == nueva:
        raise SystemExit("La key del portapapeles es la misma que ya usa el flujo; genere una nueva.")
    print("La key vieja del flujo "
          + ("ES la misma que usa el MCP odoo-helpdesk." if vieja == key_mcp else "no es la del MCP odoo-helpdesk."))
    if a.dry_run:
        print("--dry-run: no se cambió nada.")
        return

    # respaldo con la key enmascarada
    resp = AQUI / "respaldos" / f"mesa_ayuda_odoo_{dt.date.today():%Y-%m-%d}_antes_rotar_key.json"
    texto = json.dumps({"displayName": props.get("displayName"), "state": props.get("state"),
                        "definition": definicion, "connectionReferences": props.get("connectionReferences")},
                       ensure_ascii=False, indent=2).replace(vieja, MASCARA)
    resp.write_text(texto, encoding="utf-8")
    print(f"Respaldo (key enmascarada): {resp}")

    var["value"] = nueva
    pa.update_flow(FLUJO, definition=definicion)
    print("Flujo actualizado con la key nueva.")

    # enmascarar la key vieja en los respaldos anteriores
    n = 0
    for f in (AQUI / "respaldos").glob("*.json"):
        t = f.read_text(encoding="utf-8")
        if vieja in t:
            f.write_text(t.replace(vieja, MASCARA), encoding="utf-8")
            n += 1
    print(f"Key vieja enmascarada en {n} respaldo(s).")
    print("Siguiente: verificar la próxima ejecución (list_runs) y luego revocar la key vieja en Odoo.")


if __name__ == "__main__":
    main()
