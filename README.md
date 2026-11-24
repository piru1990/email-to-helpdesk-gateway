# email-to-helpdesk-gateway

**Turns a shared mailbox into Odoo help-desk tickets without losing threads, attachments or sender identity.**

## Problem
A Power Automate flow used to *create* tickets and notes by hand: replies were duplicated, senders showed as the bot, attachments broke and everyone on To/Cc got "You have been invited to follow..." mails.

## Solution
The flow now only finds/creates the contact, downloads the **raw MIME** from Microsoft Graph (`/me/messages/{id}/$value`) and hands it to Odoo's own `mail.thread.message_process` (`helpdesk.ticket`) with a dedicated integration user. Odoo then builds the ticket, author, attachments and threading (Message-ID / References) natively.

Scripts here modify the flow definition programmatically (always exporting a backup first):
- `migrar_a_pasarela.py` - move from "create ticket" to the raw-mail gateway.
- `excepcion_chasis.py` - one-ticket-per-date exception for recurring "load X on date D" mails that reply to the same thread.
- `sin_cc_pasarela.py` - rename `To:`/`Cc:` to `X-Orig-*` in the MIME so Odoo does not subscribe or notify bystanders.
- `excluir_correo_odoo.py` - drop mail sent by the Odoo instance itself (prevents ticket loops).
- `rotar_key_flujo.py` - rotate the API key stored in the flow's connection without printing it.
- `server.py` - MCP server for Power Automate (list/export/update/resubmit flows and runs, enable/disable).

## Notes
- Idempotent scripts with `--dry-run` and `--quitar` (undo); a JSON backup of the flow is saved before every change.
- The flow GUIDs and tenant are placeholders (`<FLOW_ID>`, `<ENV_OR_TENANT_ID>`).

## Español
Pasarela de correo: el flujo ya no crea tickets; baja el correo crudo con Graph y lo entrega al `message_process` de Odoo. Los scripts editan el flujo con respaldo previo, modo `--dry-run` y reversa.

> **Note / Nota:** sanitized portfolio version of production work built for a corporate IT help desk. Company names, people, hosts, IDs and data were replaced with placeholders; secrets never lived in the code (they sit in the Windows Credential Manager). / Versión anonimizada de un trabajo real de mesa de ayuda corporativa: nombres, hosts, IDs y datos fueron reemplazados; los secretos nunca estuvieron en el código.
