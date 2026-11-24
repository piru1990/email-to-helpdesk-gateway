"""Excepción «un ticket por fecha» para las cargas de chasis Brand-X en el flujo "Mesa de Ayuda → Odoo".

Problema (18-nov-2026): un usuario manda cada carga como RESPUESTA al hilo anterior y solo cambia la
fecha del asunto («CARGAR CHASIS A ETAPAS BRAND-X 17/11» → «… 18/11»). La pasarela de Odoo enlaza por
In-Reply-To/References, así que el correo del 18/11 cayó en el ticket (ya finalizado) del 17/11.

Qué hace: después de MIME_correo, si el asunto (sin RE:/RV:/FW:) es «CARGAR CHASIS A ETAPAS BRAND-X <fecha>»,
  1. busca un ticket del equipo 35 con ESE asunto exacto creado en los últimos 60 días y, si existe, el
     Message-ID de su último correo;
  2. renombra SOLO en los encabezados del MIME In-Reply-To/References (→ X-Orig-…), de modo que Odoo no
     pueda enlazar por el hilo viejo (Message-ID, remitente, cuerpo y adjuntos quedan intactos);
  3. si hay ticket de esa fecha, antepone In-Reply-To/References con el Message-ID de su último correo
     (Odoo lo enlaza a ese ticket por el mecanismo normal de respuestas; la regla 16 lo reabre si estaba
     finalizado); si no hay, el correo va sin hilo (fecha nueva → ticket nuevo con el reparto habitual).
No se usa thread_id: message_process de Odoo 19 lo ignoró en la prueba del 19-nov-2026 (creó ticket nuevo).
Todo lo demás sigue exactamente igual (rama «else» = la acción Entregar_a_Odoo de siempre).

  python excepcion_chasis.py --dry-run     muestra qué quedaría, sin tocar nada
  python excepcion_chasis.py               respalda (key enmascarada) y aplica
  python excepcion_chasis.py --actualizar  reemplaza la versión ya aplicada por la actual de este script
  python excepcion_chasis.py --quitar      deja el flujo como estaba antes (Entregar_a_Odoo al nivel superior)

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
MASCARA = "***CLAVE-REVOCADA***"
FRASE = "CARGAR CHASIS A ETAPAS BRAND-X"
NUEVAS = ["Asunto_carga", "Es_carga_chasis"]
SEGURO = {"secureData": {"properties": ["inputs"]}}
HEADERS = {"Authorization": "bearer @{variables('OdooApiKey')}", "Content-Type": "application/json; charset=utf-8"}

ASUNTO = "coalesce(triggerBody()?['subject'], '')"
CRLF = "decodeUriComponent('%0D%0A')"
CRLF2 = "decodeUriComponent('%0D%0A%0D%0A')"
MIME = "body('MIME_correo')"
CORTE = f"indexOf({MIME}, {CRLF2})"
# MIME con In-Reply-To/References renombrados SOLO en los encabezados (antes de la primera línea en blanco)
MIME_SIN_HILO = (
    f"@if(less({CORTE}, 0), {MIME}, "
    f"concat(replace(replace(substring({MIME}, 0, {CORTE}), "
    f"concat({CRLF}, 'In-Reply-To:'), concat({CRLF}, 'X-Orig-In-Reply-To:')), "
    f"concat({CRLF}, 'References:'), concat({CRLF}, 'X-Orig-References:')), "
    f"substring({MIME}, {CORTE})))"
)
# id del ticket de esa fecha (0 si no hay o si la búsqueda falló)
ID_TICKET = ("if(equals(actions('Buscar_ticket_carga').status, 'Succeeded'), "
             "if(greater(length(body('Buscar_ticket_carga')), 0), first(body('Buscar_ticket_carga'))?['id'], 0), 0)")
ULTIMO_ID = "first(body('Buscar_mensaje_hilo'))?['message_id']"
# encabezados nuevos que enlazan el correo al ticket de esa fecha (vacío si no hay ticket)
MIME_FINAL = (
    "@concat(if(equals(actions('Buscar_mensaje_hilo').status, 'Succeeded'), "
    f"if(greater(length(body('Buscar_mensaje_hilo')), 0), "
    f"concat('In-Reply-To: ', {ULTIMO_ID}, {CRLF}, 'References: ', {ULTIMO_ID}, {CRLF}), ''), ''), "
    "outputs('MIME_sin_hilo'))"
)


def cargar_pa():
    spec = importlib.util.spec_from_file_location("pa_server", AQUI / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def respaldar(props, definicion, key):
    """Guarda el flujo tal como está, con la key enmascarada."""
    dest = AQUI / "respaldos" / f"mesa_ayuda_odoo_{dt.date.today():%Y-%m-%d}_antes_chasis.json"
    n = 2
    while dest.exists():
        dest = dest.with_name(f"mesa_ayuda_odoo_{dt.date.today():%Y-%m-%d}_antes_chasis_{n}.json")
        n += 1
    texto = json.dumps({"displayName": props.get("displayName"), "state": props.get("state"),
                        "definition": definicion, "connectionReferences": props.get("connectionReferences")},
                       ensure_ascii=False, indent=2)
    if key:
        texto = texto.replace(key, MASCARA)
    dest.write_text(texto, encoding="utf-8")
    return dest


def acciones_nuevas(entregar):
    asunto_carga = {
        "runAfter": {"MIME_correo": ["Succeeded"]},
        "type": "Compose",
        # desde «CARGAR CHASIS…» en adelante (quita RE:/RV:/FW:), sin espacios sobrantes
        "inputs": (f"@trim(replace(substring({ASUNTO}, max(0, indexOf({ASUNTO}, '{FRASE}'))), '  ', ' '))"),
    }
    es_carga = {
        "runAfter": {"Asunto_carga": ["Succeeded"]},
        "type": "If",
        "expression": {"and": [
            {"greaterOrEquals": [f"@indexOf({ASUNTO}, '{FRASE}')", 0]},
            {"lessOrEquals": [f"@indexOf({ASUNTO}, '{FRASE}')", 12]},      # solo prefijos tipo «RE: RV: »
            {"greater": ["@length(outputs('Asunto_carga'))", len(FRASE) + 1]},
            {"or": [{"contains": ["@outputs('Asunto_carga')", "/"]},
                    {"contains": ["@outputs('Asunto_carga')", "-"]}]},       # lleva fecha
        ]},
        "actions": {
            "Buscar_ticket_carga": {
                "runAfter": {},
                "type": "Http",
                "inputs": {
                    "method": "POST",
                    "uri": "https://example.odoo.com/json/2/helpdesk.ticket/search_read",
                    "headers": copy.deepcopy(HEADERS),
                    "body": {
                        "domain": ("@createArray(createArray('team_id', '=', 35), "
                                   "createArray('name', '=ilike', outputs('Asunto_carga')), "
                                   "createArray('create_date', '>=', formatDateTime(addDays(utcNow(), -60), "
                                   "'yyyy-MM-dd HH:mm:ss')))"),
                        "fields": ["id"], "limit": 1, "order": "id desc"},
                },
                "runtimeConfiguration": SEGURO,
            },
            # Message-ID del último correo del ticket de esa fecha (res_id 0 si no hay ticket → sin resultados)
            "Buscar_mensaje_hilo": {
                "runAfter": {"Buscar_ticket_carga": ["Succeeded", "Failed", "TimedOut"]},
                "type": "Http",
                "inputs": {
                    "method": "POST",
                    "uri": "https://example.odoo.com/json/2/mail.message/search_read",
                    "headers": copy.deepcopy(HEADERS),
                    "body": {
                        "domain": ("@createArray(createArray('model', '=', 'helpdesk.ticket'), "
                                   f"createArray('res_id', '=', {ID_TICKET}), "
                                   "createArray('message_type', '=', 'email'), "
                                   "createArray('message_id', '!=', false))"),
                        "fields": ["message_id"], "limit": 1, "order": "id desc"},
                },
                "runtimeConfiguration": SEGURO,
            },
            "MIME_sin_hilo": {"runAfter": {}, "type": "Compose", "inputs": MIME_SIN_HILO},
            "MIME_final": {
                "runAfter": {"Buscar_mensaje_hilo": ["Succeeded", "Failed", "TimedOut"],
                             "MIME_sin_hilo": ["Succeeded"]},
                "type": "Compose",
                "inputs": MIME_FINAL,
            },
            "Entregar_carga_chasis": {
                "runAfter": {"MIME_final": ["Succeeded"]},
                "type": "Http",
                "inputs": {
                    "method": "POST",
                    "uri": "https://example.odoo.com/json/2/mail.thread/message_process",
                    "headers": copy.deepcopy(HEADERS),
                    "body": {
                        "model": "helpdesk.ticket",
                        "message": "@outputs('MIME_final')",
                        "custom_values": copy.deepcopy(entregar["inputs"]["body"]["custom_values"]),
                    },
                },
                "runtimeConfiguration": SEGURO,
            },
        },
        "else": {"actions": {"Entregar_a_Odoo": {**entregar, "runAfter": {}}}},
    }
    return {"Asunto_carga": asunto_carga, "Es_carga_chasis": es_carga}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quitar", action="store_true", help="deja el flujo como antes de la excepción")
    ap.add_argument("--actualizar", action="store_true", help="reemplaza la excepción ya aplicada por la actual")
    a = ap.parse_args()

    pa = cargar_pa()
    props = pa.get_flow(FLUJO)["properties"]
    d = copy.deepcopy(props["definition"])
    acc = d["actions"]
    key = acc["Odoo_API_key"]["inputs"]["variables"][0]["value"]

    def retirar():
        """Quita la excepción y devuelve Entregar_a_Odoo al nivel superior."""
        entregar = copy.deepcopy(acc["Es_carga_chasis"]["else"]["actions"]["Entregar_a_Odoo"])
        entregar["runAfter"] = {"MIME_correo": ["Succeeded"]}
        for n in NUEVAS:
            acc.pop(n)
        acc["Entregar_a_Odoo"] = entregar

    if a.quitar:
        if "Es_carga_chasis" not in acc:
            raise SystemExit("El flujo no tiene la excepción; no se cambió nada.")
        retirar()
        mensaje = "Excepción retirada."
    else:
        if "Es_carga_chasis" in acc:
            if not a.actualizar:
                raise SystemExit("La excepción ya está aplicada (use --actualizar); no se cambió nada.")
            retirar()
        if "Entregar_a_Odoo" not in acc or "MIME_correo" not in acc:
            raise SystemExit("El flujo no tiene la forma esperada (pasarela); no se cambió nada.")
        entregar = acc.pop("Entregar_a_Odoo")
        acc.update(acciones_nuevas(entregar))
        mensaje = "Excepción aplicada."

    # Si la pasarela ya filtra Para/CC (sin_cc_pasarela.py), lo nuevo debe leer MIME_sin_cc y no MIME_correo
    if "MIME_sin_cc" in acc:
        from sin_cc_pasarela import redirigir
        redirigir(acc, hacia_nueva=True)

    print("Acciones del flujo:", ", ".join(acc))
    if a.dry_run:
        print("--dry-run: no se cambió nada.")
        return
    print("Respaldo (key enmascarada):", respaldar(props, props["definition"], key))
    pa.update_flow(FLUJO, definition=d)
    print(mensaje)


if __name__ == "__main__":
    main()
