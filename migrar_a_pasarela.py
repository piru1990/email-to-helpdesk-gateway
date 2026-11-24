"""Migra el flujo "Mesa de Ayuda → Odoo" a la pasarela de correo nativa de Odoo (mail.thread.message_process).

Antes: el flujo creaba el ticket/nota con la API key de el supervisor (autor = el supervisor; respuestas por asunto).
Ahora: el flujo (1) busca/crea el contacto del remitente bajo ACME/Sub-B como siempre,
(2) descarga el correo crudo (MIME) con Graph y (3) se lo entrega a Odoo con la key del usuario de
integración "Mesa de Ayuda (correo)" (id 3589). Odoo crea el ticket o enlaza la respuesta por
Message-ID/References y trae los adjuntos. La regla "(CN003) Repartir y clasificar" hace el resto.

La key del bot se toma del Administrador de credenciales (odoo-helpdesk/bot-correo-api-key); nunca se
muestra. Enmascara la key vieja (la de el supervisor) en los respaldos de respaldos\\.

  python migrar_a_pasarela.py --dry-run   muestra qué quedaría
  python migrar_a_pasarela.py             aplica (el flujo debe haberse respaldado antes con export_flow)
"""
import argparse
import copy
import importlib.util
import json
import sys
import urllib.request
from pathlib import Path

import win32cred

AQUI = Path(__file__).resolve().parent
FLUJO = "<FLOW_ID>"
MASCARA = "***CLAVE-REVOCADA***"
TARGET_BOT = "odoo-helpdesk/bot-correo-api-key"
BORRAR = ["Inicializar_Asunto", "Quitar_prefijos_RE_RV_FW", "Asunto_final", "Buscar_ticket_abierto",
          "Existe_ticket_abierto", "Ticket_id", "Subir_adjuntos"]


def cargar_pa():
    spec = importlib.util.spec_from_file_location("pa_server", AQUI / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def key_bot() -> str:
    b = win32cred.CredRead(TARGET_BOT, win32cred.CRED_TYPE_GENERIC)["CredentialBlob"]
    return b.decode("utf-16-le") if isinstance(b, bytes) else str(b)


def probar(key: str) -> int:
    req = urllib.request.Request("https://example.odoo.com/json/2/res.users/context_get", data=b"{}", method="POST",
                                 headers={"Authorization": f"bearer {key}", "Content-Type": "application/json",
                                          "User-Agent": "odoo-mcp"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8")).get("uid")


SEGURO = {"secureData": {"properties": ["inputs"]}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    nueva = key_bot()
    uid = probar(nueva)
    if uid != 3589:
        raise SystemExit(f"La key del almacén no es la del usuario 3589 (uid {uid}); no se cambió nada.")
    print("Key del bot válida (uid 3589).")

    pa = cargar_pa()
    flujo = pa.get_flow(FLUJO)
    props = flujo["properties"]
    d = copy.deepcopy(props["definition"])
    acc = d["actions"]
    if "Entregar_a_Odoo" in acc:
        raise SystemExit("El flujo ya está migrado.")
    var = acc["Odoo_API_key"]["inputs"]["variables"][0]
    vieja = var["value"]

    # contacto: sacar del "else" las dos acciones que se conservan
    otro = acc["Existe_ticket_abierto"]["else"]["actions"]
    buscar = copy.deepcopy(otro["Buscar_contacto_correo"])
    falta = copy.deepcopy(otro["Falta_contacto"])
    buscar["runAfter"] = {"Empresa_grupo": ["Succeeded"]}
    falta["runAfter"] = {"Buscar_contacto_correo": ["Succeeded"]}

    for n in BORRAR:
        acc.pop(n, None)
    acc["Leer_remitente"]["runAfter"] = {"Odoo_API_key": ["Succeeded"]}
    acc["Buscar_contacto_correo"] = buscar
    acc["Falta_contacto"] = falta
    var["value"] = nueva

    acc["MIME_correo"] = {
        "runAfter": {"Falta_contacto": ["Succeeded"]},
        "type": "OpenApiConnection",
        "inputs": {
            "host": {"apiId": "/providers/Microsoft.PowerApps/apis/shared_office365",
                     "connectionName": "shared_office365", "operationId": "HttpRequest"},
            "parameters": {"Uri": "https://graph.microsoft.com/v1.0/me/messages/@{triggerBody()?['id']}/$value",
                           "Method": "GET", "ContentType": "application/json"},
            "authentication": "@parameters('$authentication')"},
    }
    acc["Entregar_a_Odoo"] = {
        "runAfter": {"MIME_correo": ["Succeeded"]},
        "type": "Http",
        "inputs": {
            "method": "POST",
            "uri": "https://example.odoo.com/json/2/mail.thread/message_process",
            "headers": {"Authorization": "bearer @{variables('OdooApiKey')}",
                        "Content-Type": "application/json; charset=utf-8"},
            "body": {
                "model": "helpdesk.ticket",
                "message": "@body('MIME_correo')",
                "custom_values": {"team_id": 35, "canal_atencion_id": 2, "stage_id": 68,
                                  "cn003_fecha_cierre_ofrecida": "@{outputs('Fecha_ofrecida')}"}}},
        "runtimeConfiguration": SEGURO,
    }

    print("Acciones nuevas:", ", ".join(acc))
    if a.dry_run:
        print("--dry-run: no se cambió nada.")
        return
    pa.update_flow(FLUJO, definition=d)
    print("Flujo actualizado a la pasarela.")

    n = 0
    for f in (AQUI / "respaldos").glob("*.json"):
        t = f.read_text(encoding="utf-8")
        if vieja and vieja in t:
            f.write_text(t.replace(vieja, MASCARA), encoding="utf-8")
            n += 1
    print(f"Key vieja enmascarada en {n} respaldo(s).")


if __name__ == "__main__":
    main()
