"""El flujo «Mesa de Ayuda → Odoo» ignora los correos que manda el propio Odoo (@example-cloud.com).

Problema (23-nov-2026): Odoo manda sus correos desde odoo-notifications@example-cloud.com («(CN003) Resumen diario de partes -
Supervisor», «Recordatorio de que debe registrar sus hojas de horas», avisos de seguidor). Caen en la carpeta
«Mesa de Ayuda» del buzón de el supervisor, que vigila el flujo, y los que no pertenecen a un ticket existente crean un
ticket nuevo que la regla 11 reparte a un técnico (#NNNNN, #NNNNN, #NNNNN, #NNNNN, #NNNNN).

Qué hace: agrega a la condición del disparador (`conditions[0].expression`) el filtro
  not(endsWith(toLower(coalesce(triggerBody()?['from'], '')), '@example-cloud.com'))
justo después del de «@example-cloud2.com». No toca ninguna acción (MIME_sin_cc, Es_carga_chasis y Entregar_a_Odoo
quedan igual). Idempotente: si el filtro ya está, no hace nada.

  python excluir_correo_odoo.py --dry-run   muestra la condición que quedaría, sin tocar nada
  python excluir_correo_odoo.py             respalda (key enmascarada) y aplica
  python excluir_correo_odoo.py --quitar    retira el filtro

Nunca imprime la API key. NO usar get_flow/export_flow del MCP en este flujo: dejan la key en claro.
"""
import argparse
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path

AQUI = Path(__file__).resolve().parent
FLUJO = "<FLOW_ID>"
DISPARADOR = "Correo_nuevo_para_Mesa_de_Ayuda"
MASCARA = "***CLAVE-REVOCADA***"
DOMINIO = "@example-cloud.com"
FILTRO = f"not(endsWith(toLower(coalesce(triggerBody()?['from'], '')), '{DOMINIO}'))"
ANCLA = "not(endsWith(toLower(coalesce(triggerBody()?['from'], '')), '@example-cloud2.com')), "


def agregar(expr: str) -> str:
    """Inserta el filtro después del de example-cloud2.com. Idempotente."""
    if FILTRO in expr:
        return expr
    if ANCLA not in expr:
        raise SystemExit("La condición del disparador no tiene la forma esperada (falta el filtro de acmecloud); no se cambió nada.")
    return expr.replace(ANCLA, ANCLA + FILTRO + ", ", 1)


def quitar(expr: str) -> str:
    return expr.replace(FILTRO + ", ", "", 1) if FILTRO in expr else expr


def cargar_pa():
    spec = importlib.util.spec_from_file_location("pa_server", AQUI / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def respaldar(props, definicion, key):
    """Guarda el flujo tal como está, con la key enmascarada."""
    dest = AQUI / "respaldos" / f"mesa_ayuda_odoo_{dt.date.today():%Y-%m-%d}_antes_excluir_odoo.json"
    n = 2
    while dest.exists():
        dest = dest.with_name(f"mesa_ayuda_odoo_{dt.date.today():%Y-%m-%d}_antes_excluir_odoo_{n}.json")
        n += 1
    dest.parent.mkdir(exist_ok=True)
    texto = json.dumps({"displayName": props.get("displayName"), "state": props.get("state"),
                        "definition": definicion, "connectionReferences": props.get("connectionReferences")},
                       ensure_ascii=False, indent=2)
    if key:
        texto = texto.replace(key, MASCARA)
    dest.write_text(texto, encoding="utf-8")
    return dest


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quitar", action="store_true")
    a = ap.parse_args()

    pa = cargar_pa()
    props = pa.get_flow(FLUJO)["properties"]
    original = props["definition"]
    d = copy.deepcopy(original)
    key = d["actions"]["Odoo_API_key"]["inputs"]["variables"][0]["value"]

    trig = d["triggers"].get(DISPARADOR)
    if not trig or not trig.get("conditions"):
        raise SystemExit("El flujo no tiene la forma esperada (falta el disparador o su condición); no se cambió nada.")
    expr = trig["conditions"][0]["expression"]
    nueva = quitar(expr) if a.quitar else agregar(expr)

    if nueva == expr:
        print("El filtro ya estaba así; no se cambió nada.")
        return
    trig["conditions"][0]["expression"] = nueva
    print("Condición del disparador que quedaría:\n")
    for parte in nueva.removeprefix("@and(").removesuffix(")").split("), not("):
        print("  ", parte if parte.startswith("not(") else "not(" + parte)
    if a.dry_run:
        print("\n--dry-run: no se cambió nada.")
        return
    print("\nRespaldo (key enmascarada):", respaldar(props, original, key))
    pa.update_flow(FLUJO, definition=d)
    print("Filtro retirado." if a.quitar else "Filtro aplicado.")


if __name__ == "__main__":
    main()
