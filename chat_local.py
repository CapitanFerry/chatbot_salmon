import requests
import sys

BASE_URL = "http://127.0.0.1:8000/docs"
SENDER = "+51999999999"  # simulamos siempre al mismo cliente

print("Chat local con el bot de salmón 🐟 (escribe 'salir' para terminar)\n")

while True:
    try:
        user_text = input("Cliente: ")
    except EOFError:
        print("\nEOF recibido, saliendo.")
        break

    if user_text.strip().lower() in ["salir", "exit", "quit"]:
        print("Fin del chat.")
        break

    payload = {
        "sender": SENDER,
        "text": user_text
    }

    try:
        print("Enviando al backend...", payload)  # DEBUG
        resp = requests.post(BASE_URL, json=payload, timeout=10)
        print("Status code:", resp.status_code)   # DEBUG

        resp.raise_for_status()
        data = resp.json()
        reply = data.get("reply", "(sin respuesta)")
        print(f"Bot   : {reply}\n")
    except Exception as e:
        print("Error llamando al backend:", repr(e))
        sys.exit(1)
