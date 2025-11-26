from fastapi import FastAPI, Request, Response
from pydantic import BaseModel
from datetime import datetime
import csv
import os
import json
from dotenv import load_dotenv
from openai import OpenAI
import requests

# ==========================
# Configuración inicial
# ==========================

load_dotenv(override=True)



WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN")
WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "salmon_verify_123")

WHATSAPP_API_URL = (
    f"https://graph.facebook.com/v22.0/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    if WHATSAPP_PHONE_NUMBER_ID
    else None
)

print("WHATSAPP_PHONE_NUMBER_ID:", WHATSAPP_PHONE_NUMBER_ID)
print("WHATSAPP_API_URL:", WHATSAPP_API_URL)
print("WHATSAPP_ACCESS_TOKEN (primeros 10):", (WHATSAPP_ACCESS_TOKEN or "")[:10])

client = OpenAI()
app = FastAPI()

ORDERS_FILE = "pedidos.csv"

# Estado por cliente (vive en memoria mientras el proceso está levantado)
SESSIONS: dict[str, dict] = {}


# ==========================
# Modelos
# ==========================

class IncomingMessage(BaseModel):
    sender: str
    text: str


# ==========================
# Lógica de negocio
# ==========================

def estado_inicial() -> dict:
    return {
        "cantidad_kg": None,
        "dia_entrega": None,
        "direccion": None,
        "distrito": None,
        "metodo_pago": None,
        "confirmado": False,
    }


def guardar_pedido(telefono: str, estado: dict, mensaje_original: str) -> None:
    file_exists = os.path.exists(ORDERS_FILE)

    with open(ORDERS_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)

        if not file_exists:
            writer.writerow(
                [
                    "timestamp",
                    "telefono",
                    "mensaje_original",
                    "cantidad_kg",
                    "dia_entrega",
                    "direccion",
                    "distrito",
                    "metodo_pago",
                ]
            )

        writer.writerow(
            [
                datetime.now().isoformat(sep=" ", timespec="seconds"),
                telefono,
                mensaje_original,
                estado.get("cantidad_kg", ""),
                estado.get("dia_entrega", ""),
                estado.get("direccion", ""),
                estado.get("distrito", ""),
                estado.get("metodo_pago", ""),
            ]
        )


def llamar_agente_ia(telefono: str, mensaje: str, estado_actual: dict) -> dict:
    """
    El agente de IA recibe el mensaje y el estado actual,
    actualiza variables, identifica faltantes, y genera la respuesta.
    """

    system_prompt = """
Eres un agente encargado de tomar pedidos de filete de salmón para un negocio pequeño en Lima.

Recibes SIEMPRE un objeto JSON con:
- "telefono": número del cliente
- "estado_actual": diccionario con los datos que ya conocemos de este pedido
- "mensaje_nuevo": texto más reciente que envió el cliente

Debes **extraer o solicitar** los siguientes datos:
- cantidad_kg (float)
- dia_entrega (texto: "hoy", "mañana", fecha, etc.)
- direccion (texto)
- distrito (texto)
- metodo_pago ("yape", "plin", "efectivo", "transferencia", etc.)
- confirmado (true/false)

REGLAS IMPORTANTES:

- Si el mensaje nuevo contiene algún dato (por ejemplo "2 kilos", "mañana en la noche", "Surco",
  "pago con Yape", etc.), debes extraerlo y colocarlo en el JSON de salida.
- Usa siempre "estado_actual" como base. Si un campo ya tiene valor en "estado_actual" y el cliente
  no lo modifica, NO lo pongas como null ni lo borres.
- Solo cambia un valor si el cliente lo corrige explícitamente (por ejemplo, "mejor que sean 3 kilos").
- Solo pregunta por los campos que realmente falten.
- NO marques confirmado=true hasta que TODOS los campos estén completos y el cliente exprese
  claramente que desea confirmar (por ejemplo: "sí, confirma", "está bien así", "adelante con el pedido").
- Si falta información, explica claramente qué falta (ejemplo: dirección, distrito, método de pago).

TU RESPUESTA DEBE SER **SOLO** UN JSON VÁLIDO con este formato EXACTO:

{
  "cantidad_kg": float or null,
  "dia_entrega": string or null,
  "direccion": string or null,
  "distrito": string or null,
  "metodo_pago": string or null,
  "confirmado": boolean,
  "campos_faltantes": [string, ...],
  "respuesta_para_usuario": string
}

- "campos_faltantes" debe listar exactamente los nombres de los campos que aún faltan.
- "respuesta_para_usuario" debe ser un texto amable en español, hablando como un humano
  de un pequeño negocio de salmón en Lima.
- NO AGREGUES TEXTO FUERA DEL JSON.
"""

    user_message = {
        "telefono": telefono,
        "estado_actual": estado_actual,
        "mensaje_nuevo": mensaje,
    }

    completion = client.chat.completions.create(
        model="gpt-4.1-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_message, ensure_ascii=False)},
        ],
        temperature=0.2,
    )

    contenido = completion.choices[0].message.content

    try:
        data = json.loads(contenido)
    except json.JSONDecodeError:
        # Fallback si el modelo responde algo raro
        data = {
            "cantidad_kg": estado_actual.get("cantidad_kg"),
            "dia_entrega": estado_actual.get("dia_entrega"),
            "direccion": estado_actual.get("direccion"),
            "distrito": estado_actual.get("distrito"),
            "metodo_pago": estado_actual.get("metodo_pago"),
            "confirmado": False,
            "campos_faltantes": [
                "cantidad_kg",
                "dia_entrega",
                "direccion",
                "distrito",
                "metodo_pago",
            ],
            "respuesta_para_usuario": (
                "Tuve un problema interpretando tu mensaje 🤖. "
                "¿Cuántos kilos de filete de salmón deseas?"
            ),
        }

    return data


def procesar_mensaje_bot(sender: str, text: str) -> str:
    """
    Orquesta todo el flujo del bot:
    - Recupera o crea el estado del cliente
    - Llama al agente de IA
    - Actualiza el estado sin pisar valores con None
    - Guarda el pedido si está confirmado
    - Devuelve el texto de respuesta para el usuario
    """
    estado = SESSIONS.get(sender)
    if estado is None:
        estado = estado_inicial()

    resultado = llamar_agente_ia(sender, text or "", estado)

    # Mezcla inteligente: solo pisar si el valor NO es None
    for clave in estado.keys():
        if clave in resultado:
            nuevo_valor = resultado[clave]
            if nuevo_valor is not None:
                estado[clave] = nuevo_valor

    SESSIONS[sender] = estado

    campos_faltantes = resultado.get("campos_faltantes", [])
    confirmado = resultado.get("confirmado", False)

    if confirmado and not campos_faltantes:
        guardar_pedido(sender, estado, text or "")
        print(f"Pedido guardado para {sender}")

    return resultado.get("respuesta_para_usuario", "Recibido 👍")


# ==========================
# Endpoints
# ==========================

@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "Bot de pedidos de salmón está vivo 🐟",
        "endpoints": ["/webhook", "/whatsapp-webhook", "/docs"],
    }


# --- Endpoint local para pruebas (chat_local.py, /docs) ---

@app.post("/webhook")
def receive_message(msg: IncomingMessage):
    print("Nuevo mensaje LOCAL:")
    print(f"De: {msg.sender}")
    print(f"Texto: {msg.text}")

    reply = procesar_mensaje_bot(msg.sender, msg.text or "")
    return {"reply": reply}


# --- Webhook para WhatsApp Cloud API ---

@app.get("/whatsapp-webhook")
async def verify_whatsapp_webhook(request: Request):
    """
    Verificación de webhook por parte de Meta.
    Meta hace un GET con hub.mode, hub.verify_token y hub.challenge.
    Si el token coincide, devolvemos el challenge.
    """
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    print("GET /whatsapp-webhook", dict(request.query_params))

    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        return Response(content=challenge or "", media_type="text/plain")

    return Response(status_code=403)


@app.post("/whatsapp-webhook")
async def whatsapp_webhook(request: Request):
    """
    Maneja mensajes entrantes desde WhatsApp Cloud API.
    """
    data = await request.json()
    print("Webhook WhatsApp recibido:")
    print(json.dumps(data, ensure_ascii=False, indent=2))

    try:
        entry = data.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
    except (IndexError, KeyError, AttributeError) as e:
        print("Payload no tiene formato esperado:", e)
        return {"status": "ignored"}

    if not messages:
        return {"status": "no_messages"}

    message = messages[0]
    sender = message.get("from")  # número del cliente (ej. "51959447537")
    msg_type = message.get("type")

    if msg_type != "text":
        print("Tipo de mensaje no soportado:", msg_type)
        return {"status": "unsupported_message_type"}

    text = message["text"]["body"]
    print(f"Mensaje de {sender}: {text}")

    # Usa el mismo flujo de siempre (estado + IA + CSV)
    reply_text = procesar_mensaje_bot(sender, text)

    if not WHATSAPP_API_URL or not WHATSAPP_ACCESS_TOKEN:
        print("Falta configurar WHATSAPP_API_URL o WHATSAPP_ACCESS_TOKEN")
        return {"status": "whatsapp_not_configured"}

    payload = {
        "messaging_product": "whatsapp",
        "to": sender,
        "type": "text",
        "text": {"body": reply_text},
    }
    headers = {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    resp = requests.post(WHATSAPP_API_URL, headers=headers, json=payload)
    print("Respuesta envío WhatsApp:", resp.status_code, resp.text)

    return {"status": "ok"}
