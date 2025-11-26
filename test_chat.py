print(">>> test_chat.py está corriendo")

while True:
    txt = input("Escribe algo (o 'salir'): ")
    if txt.lower() == "salir":
        print("Chau 👋")
        break
    print("Tú escribiste:", txt)
