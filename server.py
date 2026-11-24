"""MCP: Power Automate (API de administración de flujos, api.flow.microsoft.com).

Sesión: `python ..\\_msauth.py login` una vez (navegador). delete_flow solo borra flujos apagados
y exige el nombre exacto.
Variable opcional: PP_ENVIRONMENT (por defecto el entorno Default de Acme).
"""
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import _msauth  # noqa: E402

mcp = FastMCP("power-automate")
RO = ToolAnnotations(readOnlyHint=True)

ENV = os.environ.get("PP_ENVIRONMENT", "Default-<ENV_OR_TENANT_ID>")
BASE = "https://api.flow.microsoft.com/providers/Microsoft.ProcessSimple"
API = {"api-version": "2016-11-01"}
SCOPE = _msauth.SCOPES["flow"]


def _flows_url(environment: str, suffix: str = "") -> str:
    return f"{BASE}/environments/{environment or ENV}/flows{suffix}"


def _brief(flow: dict) -> dict:
    p = flow.get("properties", {})
    return {"id": flow.get("name"), "name": p.get("displayName"), "state": p.get("state"),
            "created": p.get("createdTime"), "modified": p.get("lastModifiedTime"),
            "trigger": [t.get("kind") or t.get("type") for t in p.get("definitionSummary", {}).get("triggers", [])]}


def _all(url: str, params: dict, max_items: int) -> list[dict]:
    """Recorre las páginas (nextLink) hasta juntar max_items."""
    out: list[dict] = []
    data = _msauth.api("GET", url, SCOPE, params)
    while True:
        out += data.get("value", [])
        siguiente = data.get("nextLink") or data.get("@odata.nextLink")
        if not siguiente or len(out) >= max_items:
            return out[:max_items]
        data = _msauth.api("GET", siguiente, SCOPE)


def _check_definition(definition: dict) -> None:
    """Errores de definición que la API rechaza con mensajes poco claros (InvalidSecureDataConfiguration)."""
    problemas = []

    def walk(acciones: dict, ruta: str):
        for nombre, a in (acciones or {}).items():
            if not isinstance(a, dict):
                continue
            props = (a.get("runtimeConfiguration", {}).get("secureData", {}) or {}).get("properties", [])
            if a.get("type") == "Compose" and "outputs" in props:
                problemas.append(f"{ruta}{nombre} (Compose): secureData solo admite ['inputs'], no 'outputs'. "
                                 "Para ocultar un secreto, marque 'inputs' aquí y en las acciones que lo usan.")
            for clave in ("actions", "else", "default"):
                hijo = a.get(clave)
                walk(hijo.get("actions", hijo) if isinstance(hijo, dict) and clave != "actions" else hijo,
                     f"{ruta}{nombre}/")
            for caso in (a.get("cases") or {}).values():
                walk(caso.get("actions"), f"{ruta}{nombre}/")

    walk(definition.get("actions"), "")
    if problemas:
        raise ValueError("Definición inválida:\n- " + "\n- ".join(problemas))


@mcp.tool(annotations=RO)
def list_flows(environment: str = "", max_items: int = 200) -> list[dict]:
    """Flujos propios del usuario en el entorno (id, nombre, estado)."""
    return [_brief(f) for f in _all(_flows_url(environment), API, max_items)]


@mcp.tool(annotations=RO)
def get_flow(flow_id: str, environment: str = "") -> dict:
    """Flujo completo: definición (disparador y acciones) y referencias de conexión."""
    return _msauth.api("GET", _flows_url(environment, f"/{flow_id}"), SCOPE, API)


@mcp.tool(annotations=RO)
def list_runs(flow_id: str, top: int = 20, environment: str = "") -> list[dict]:
    """Últimas ejecuciones de un flujo (estado, inicio, fin, error, disparador). top hasta 250."""
    top = max(1, min(top, 250))
    out = []
    for r in _all(_flows_url(environment, f"/{flow_id}/runs"), {**API, "$top": min(top, 50)}, top):
        p = r.get("properties", {})
        out.append({"id": r.get("name"), "status": p.get("status"), "start": p.get("startTime"),
                    "end": p.get("endTime"), "error": p.get("error"),
                    "trigger": (p.get("trigger") or {}).get("name")})
    return out


@mcp.tool(annotations=RO)
def get_run(flow_id: str, run_id: str, environment: str = "") -> dict:
    """Detalle de una ejecución."""
    return _msauth.api("GET", _flows_url(environment, f"/{flow_id}/runs/{run_id}"), SCOPE, API)


@mcp.tool(annotations=RO)
def list_connections(environment: str = "") -> list[dict]:
    """Conexiones del usuario en el entorno (para armar connectionReferences de un flujo)."""
    data = _msauth.api("GET", "https://api.powerapps.com/providers/Microsoft.PowerApps/connections",
                       _msauth.SCOPES["powerapps"],
                       {**API, "$filter": f"environment eq '{environment or ENV}'"})
    out = []
    for c in data.get("value", []):
        p = c.get("properties", {})
        out.append({"name": c.get("name"), "api": p.get("apiId", "").rsplit("/", 1)[-1],
                    "display": p.get("displayName"),
                    "status": [s.get("status") for s in p.get("statuses", [])]})
    return out


@mcp.tool()
def create_flow(display_name: str, definition: dict, connection_references: dict | None = None,
                start: bool = False, environment: str = "") -> dict:
    """Crea un flujo a partir de una definición de Logic Apps. Por defecto queda APAGADO (start=False).
    Para ocultar secretos en el historial use runtimeConfiguration.secureData.properties: en acciones
    Compose solo se admite ["inputs"]; en HTTP, ["inputs"] y/o ["outputs"]."""
    _check_definition(definition)
    body: dict[str, Any] = {"properties": {
        "displayName": display_name, "definition": definition,
        "connectionReferences": connection_references or {},
        "state": "Started" if start else "Stopped"}}
    return _brief(_msauth.api("POST", _flows_url(environment), SCOPE, API, body))


@mcp.tool()
def update_flow(flow_id: str, display_name: str = "", definition: dict | None = None,
                connection_references: dict | None = None, environment: str = "") -> dict:
    """Actualiza nombre, definición y/o referencias de conexión de un flujo existente."""
    props: dict[str, Any] = {}
    if display_name:
        props["displayName"] = display_name
    if definition is not None:
        _check_definition(definition)
        props["definition"] = definition
    if connection_references is not None:
        props["connectionReferences"] = connection_references
    if not props:
        raise ValueError("Nada que actualizar")
    return _brief(_msauth.api("PATCH", _flows_url(environment, f"/{flow_id}"), SCOPE, API, {"properties": props}))


@mcp.tool()
def set_flow_state(flow_id: str, on: bool, environment: str = "") -> dict:
    """Enciende (on=True) o apaga (on=False) un flujo."""
    _msauth.api("POST", _flows_url(environment, f"/{flow_id}/{'start' if on else 'stop'}"), SCOPE, API)
    return _brief(_msauth.api("GET", _flows_url(environment, f"/{flow_id}"), SCOPE, API))


@mcp.tool()
def export_flow(flow_id: str, dest_path: str, environment: str = "") -> dict:
    """Guarda en un archivo JSON local la definición y las referencias de conexión de un flujo
    (respaldo antes de modificarlo, o base para create_flow). No sobrescribe."""
    import json
    destino = Path(dest_path)
    if destino.exists():
        raise FileExistsError(f"{destino} ya existe; elija otro nombre")
    p = _msauth.api("GET", _flows_url(environment, f"/{flow_id}"), SCOPE, API).get("properties", {})
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"displayName": p.get("displayName"), "state": p.get("state"),
                                   "definition": p.get("definition"),
                                   "connectionReferences": p.get("connectionReferences")},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
    return {"path": str(destino), "name": p.get("displayName"),
            "actions": len((p.get("definition") or {}).get("actions", {}))}


@mcp.tool()
def resubmit_run(flow_id: str, run_id: str, environment: str = "") -> dict:
    """Vuelve a ejecutar un flujo con los mismos datos del disparador de una ejecución anterior."""
    run = _msauth.api("GET", _flows_url(environment, f"/{flow_id}/runs/{run_id}"), SCOPE, API)
    trigger = (run.get("properties", {}).get("trigger") or {}).get("name")
    if not trigger:
        raise ValueError("No pude determinar el disparador de esa ejecución")
    _msauth.api("POST", _flows_url(environment, f"/{flow_id}/triggers/{trigger}/histories/{run_id}/resubmit"),
                SCOPE, API)
    return {"flow": flow_id, "resubmitted_from": run_id, "trigger": trigger}


@mcp.tool()
def cancel_run(flow_id: str, run_id: str, environment: str = "") -> dict:
    """Cancela una ejecución en curso."""
    _msauth.api("POST", _flows_url(environment, f"/{flow_id}/runs/{run_id}/cancel"), SCOPE, API)
    return {"flow": flow_id, "cancelled": run_id}


@mcp.tool()
def delete_flow(flow_id: str, confirm_name: str, environment: str = "") -> dict:
    """BORRA un flujo de forma permanente. Solo si está APAGADO y confirm_name es exactamente su nombre.
    Antes de borrar conviene export_flow."""
    f = _msauth.api("GET", _flows_url(environment, f"/{flow_id}"), SCOPE, API)
    p = f.get("properties", {})
    if p.get("displayName") != confirm_name:
        raise ValueError(f"confirm_name no coincide con el nombre del flujo ('{p.get('displayName')}')")
    if p.get("state") != "Stopped":
        raise ValueError("El flujo está encendido; apáguelo primero con set_flow_state")
    _msauth.api("DELETE", _flows_url(environment, f"/{flow_id}"), SCOPE, API)
    return {"deleted": flow_id, "name": confirm_name}


if __name__ == "__main__":
    mcp.run()
