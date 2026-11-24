"""La pasarela de correo ya no le muestra a Odoo los destinatarios (Para/CC) del correo.

Problema (20-nov-2026): el gerente recibió «You have been invited to follow RV: … (#NNNNN)» porque iba en
Para/CC del correo que originó el ticket. Odoo suscribe a todos los destinatarios al crear el ticket y les
manda ese aviso en el acto; la regla 13 («No seguir tickets: excluidos») los quita DESPUÉS, cuando el aviso ya
salió. Lo mismo les llega a todos los CC de cualquier correo a mesadeayuda@.

Qué hace: antes de entregar el correo a Odoo, en los ENCABEZADOS del MIME (hasta la primera línea en blanco)
renombra «To:» y «Cc:» a «X-Orig-To:» y «X-Orig-Cc:». Remitente, asunto, Message-ID, In-Reply-To/References,
cuerpo y adjuntos quedan intactos (el enlace de respuestas por hilo sigue igual). El ticket se crea con
`model` y `custom_values` explícitos, así que Odoo no necesita el «To:».

Agrega la acción Compose `MIME_sin_cc` (después de MIME_correo) y apunta a ella todo lo que leía
`body('MIME_correo')` (Entregar_a_Odoo y, si está aplicada, la excepción de chasis).

  python sin_cc_pasarela.py --dry-run     muestra qué quedaría, sin tocar nada
  python sin_cc_pasarela.py               respalda (key enmascarada) y aplica
  python sin_cc_pasarela.py --actualizar  reemplaza la versión ya aplicada por la actual
  python sin_cc_pasarela.py --quitar      deja el flujo como estaba (todo vuelve a leer MIME_correo)
  python sin_cc_pasarela.py --probar      prueba la lógica de encabezados en Python (sin tocar nada)

Nunca imprime la API key. NO usar get_flow/export_flow del MCP en este flujo: dejan la key en claro.
`excepcion_chasis.py` llama a `redirigir()` para no perder este paso si se vuelve a aplicar.
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
NUEVA = "MIME_sin_cc"
ORIGEN = "MIME_correo"
LEE_ORIGEN = "body('MIME_correo')"
LEE_NUEVA = "outputs('MIME_sin_cc')"

CRLF = "decodeUriComponent('%0D%0A')"
CRLF2 = "decodeUriComponent('%0D%0A%0D%0A')"
MIME = f"body('{ORIGEN}')"
CORTE = f"indexOf({MIME}, {CRLF2})"
# (encabezado original, renombrado); Exchange suele escribir «Cc:» pero se cubren las variantes de mayúsculas
PARES = [(t, f"X-Orig-{t}") for t in ("To", "to", "TO", "Cc", "CC", "cc")]


def expresion_sin_cc() -> str:
    """Compose: MIME con To:/Cc: renombrados SOLO en los encabezados (antes de la primera línea en blanco)."""
    # se antepone un CRLF para que también se renombre un «To:» que sea la primera línea
    enc = f"concat({CRLF}, substring({MIME}, 0, {CORTE}))"
    for a, b in PARES:
        enc = f"replace({enc}, concat({CRLF}, '{a}:'), concat({CRLF}, '{b}:'))"
    return f"@if(less({CORTE}, 0), {MIME}, concat(substring({enc}, 2), substring({MIME}, {CORTE})))"


def accion_nueva() -> dict:
    return {"runAfter": {ORIGEN: ["Succeeded"]}, "type": "Compose", "inputs": expresion_sin_cc()}


def redirigir(acc: dict, hacia_nueva: bool = True) -> None:
    """Apunta todo lo que leía MIME_correo a MIME_sin_cc (o al revés). Idempotente."""
    de, a = (LEE_ORIGEN, LEE_NUEVA) if hacia_nueva else (LEE_NUEVA, LEE_ORIGEN)
    nueva = acc.pop(NUEVA, None)  # no se toca a sí misma
    texto = json.dumps(acc, ensure_ascii=False).replace(de, a)
    acc.clear()
    acc.update(json.loads(texto))
    if hacia_nueva and nueva is not None:
        acc[NUEVA] = nueva
    de_ra, a_ra = (ORIGEN, NUEVA) if hacia_nueva else (NUEVA, ORIGEN)
    for nombre, accion in acc.items():
        ra = accion.get("runAfter") or {}
        if nombre != NUEVA and de_ra in ra:
            accion["runAfter"] = {(a_ra if k == de_ra else k): v for k, v in ra.items()}


def simular(mime: str) -> str:
    """Misma lógica que la expresión de Power Automate, para probarla aquí."""
    corte = mime.find("\r\n\r\n")
    if corte < 0:
        return mime
    enc = "\r\n" + mime[:corte]
    for a, b in PARES:
        enc = enc.replace(f"\r\n{a}:", f"\r\n{b}:")
    return enc[2:] + mime[corte:]


def probar() -> None:
    cuerpo = "\r\n\r\nHola\r\nTo: esto es cuerpo, no se toca\r\nCc: tampoco\r\n"
    casos = {
        "To primero": "To: a@x.com\r\nFrom: b@x.com\r\nSubject: s\r\n" + cuerpo,
        "To/CC plegados": "From: b@x.com\r\nTo: a@x.com,\r\n c@x.com\r\nCC: d@x.com\r\nDelivered-To: z@x.com\r\n"
                          "In-Reply-To: <1@x>\r\nSubject: s" + cuerpo,
        "sin encabezados To/Cc": "From: b@x.com\r\nSubject: s" + cuerpo,
        "sin cuerpo (sin línea en blanco)": "To: a@x.com\r\nFrom: b@x.com",
    }
    ok = True
    for nombre, mime in casos.items():
        r = simular(mime)
        corte = mime.find("\r\n\r\n")
        cuerpo_igual = corte < 0 or r.endswith(mime[corte:])
        print(f"--- {nombre} (cuerpo intacto: {cuerpo_igual})\n{r.split(chr(13) + chr(10) + chr(13) + chr(10))[0]}")
        ok = ok and cuerpo_igual
    r = simular(casos["To/CC plegados"])
    ok = ok and "\r\nTo:" not in r.split("\r\n\r\n")[0] and "Delivered-To: z@x.com" in r
    ok = ok and simular(casos["To primero"]).startswith("X-Orig-To: a@x.com")
    print("\nPRUEBA", "OK" if ok else "FALLÓ")


def cargar_pa():
    spec = importlib.util.spec_from_file_location("pa_server", AQUI / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def respaldar(props, definicion, key):
    """Guarda el flujo tal como está, con la key enmascarada."""
    dest = AQUI / "respaldos" / f"mesa_ayuda_odoo_{dt.date.today():%Y-%m-%d}_antes_sin_cc.json"
    n = 2
    while dest.exists():
        dest = dest.with_name(f"mesa_ayuda_odoo_{dt.date.today():%Y-%m-%d}_antes_sin_cc_{n}.json")
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
    ap.add_argument("--actualizar", action="store_true")
    ap.add_argument("--probar", action="store_true")
    a = ap.parse_args()
    if a.probar:
        probar()
        return

    pa = cargar_pa()
    props = pa.get_flow(FLUJO)["properties"]
    d = copy.deepcopy(props["definition"])
    acc = d["actions"]
    key = acc["Odoo_API_key"]["inputs"]["variables"][0]["value"]

    if ORIGEN not in acc:
        raise SystemExit("El flujo no tiene la forma esperada (falta MIME_correo); no se cambió nada.")

    if a.quitar:
        if NUEVA not in acc:
            raise SystemExit("El flujo no tiene MIME_sin_cc; no se cambió nada.")
        redirigir(acc, hacia_nueva=False)
        acc.pop(NUEVA)
        mensaje = "MIME_sin_cc retirado."
    else:
        if NUEVA in acc:
            if not a.actualizar:
                raise SystemExit("MIME_sin_cc ya está aplicado (use --actualizar); no se cambió nada.")
            redirigir(acc, hacia_nueva=False)
            acc.pop(NUEVA)
        acc[NUEVA] = accion_nueva()
        redirigir(acc, hacia_nueva=True)
        mensaje = "MIME_sin_cc aplicado."

    print("Acciones del flujo:", ", ".join(acc))
    for nombre, accion in acc.items():
        txt = json.dumps(accion, ensure_ascii=False)
        usa = [n for n in (LEE_ORIGEN, LEE_NUEVA) if n in txt]
        ra = list((accion.get("runAfter") or {}).keys())
        if usa or ORIGEN in ra or NUEVA in ra:
            print(f"  {nombre}: lee {usa or '-'}; runAfter {ra}")
    if a.dry_run:
        print("--dry-run: no se cambió nada.")
        return
    print("Respaldo (key enmascarada):", respaldar(props, props["definition"], key))
    pa.update_flow(FLUJO, definition=d)
    print(mensaje)


if __name__ == "__main__":
    main()
