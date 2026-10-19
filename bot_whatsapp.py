import os
import json
import requests
import csv
import random
from datetime import datetime
from difflib import SequenceMatcher
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv

# 1. Cargar variables
load_dotenv()
META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") # NUEVO

app = FastAPI()

# 2. Cargar JSON
with open('base_conocimiento.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
faqs = data['faqs']
conceptos_pago = data['conceptos_pago']

user_states = {}
user_data = {}

# --- 🚨 LOS ENLACES DE TUS PÓSTERS ---
POSTERS_FALLBACK = [
    {"url": "https://i.ibb.co/7tJZHVc2/1.png", "texto": "🤔 ¿Tienes alguna duda académica? ¡No busques más! Estoy aquí para responderte al instante. Escribe *menú* para empezar. 🚀"},
    {"url": "https://i.ibb.co/zhbXXXsX/2.png", "texto": "🥤 ¡La bebida oficial de la información! Refréscate con respuestas inmediatas sobre el instituto, las 24 horas del día. Escribe *menú* para tomarte una. 😉"},
    {"url": "https://i.ibb.co/LXJDyTQd/3.png", "texto": "🛡️ ¡No salgas a la superficie a hacer filas! Aquí estoy seguro, 24/7. Escribe *menú* para que te ayude con cualquier trámite. 🤖✨"}
]

# --- CARTERA DE CLIENTES ---
if not os.path.exists('logs'): os.makedirs('logs')

def guardar_cliente(numero, nombres, perfil, carrera):
    archivo = 'logs/cartera_clientes.csv'
    archivo_existe = os.path.isfile(archivo)
    with open(archivo, mode='a', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        if not archivo_existe: writer.writerow(['Fecha', 'Hora', 'WhatsApp', 'Nombres y Apellidos', 'Perfil', 'Carrera'])
        ahora = datetime.now()
        writer.writerow([ahora.strftime('%Y-%m-%d'), ahora.strftime('%H:%M:%S'), numero, nombres, perfil, carrera])

def cargar_cliente(numero):
    archivo = 'logs/cartera_clientes.csv'
    if not os.path.isfile(archivo): return None
    with open(archivo, mode='r', encoding='utf-8') as file:
        reader = csv.reader(file)
        next(reader, None)
        for row in reader:
            if len(row) >= 6 and row[2] == numero:
                return {"nombres": row[3], "perfil": row[4], "carrera": row[5]}
    return None

# --- ENVÍO DE MENSAJES E IMÁGENES ---
def enviar_mensaje_meta(numero_destino, texto):
    if not texto: return
    url = f"https://graph.facebook.com/v18.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": numero_destino, "type": "text", "text": {"body": texto}}
    requests.post(url, headers=headers, json=payload)

def enviar_imagen_meta(numero_destino, url_imagen, texto_opcional=""):
    url = f"https://graph.facebook.com/v18.0/{META_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": numero_destino, "type": "image", "image": {"link": url_imagen}}
    if texto_opcional: payload["image"]["caption"] = texto_opcional
    requests.post(url, headers=headers, json=payload)

def enviar_fallback_ciclico(numero):
    if numero not in user_data: user_data[numero] = {}
    indice_actual = user_data[numero].get('poster_indice', 0)
    poster_elegido = POSTERS_FALLBACK[indice_actual]
    user_data[numero]['poster_indice'] = (indice_actual + 1) % len(POSTERS_FALLBACK)
    enviar_imagen_meta(numero, poster_elegido["url"], poster_elegido["texto"])

# --- BÚSQUEDA SEGURA ---
def buscar_faq(termino, categoria=None):
    for f in faqs:
        if termino.lower() in f['subcategoria'].lower():
            if categoria:
                if categoria.lower() in f['categoria'].lower(): return f
            else: return f
        if termino.lower() in f['categoria'].lower(): return f
    return None

# --- 🤖 EL PODER DE LA IA (DEEPSEEK) CON PRECIOS EXACTOS ---
def redactar_con_ia(prompt_usuario, contexto_json):
    """Usa DeepSeek para redactar respuestas amigables, inyectando el precio exacto."""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    
    # Buscamos el precio exacto en el TUPA
    precio = None
    if isinstance(contexto_json, dict):
        cat = contexto_json.get('categoria', '').lower()
        subcat = contexto_json.get('subcategoria', '').lower()
        text_to_match = cat + " " + subcat
        
        for pago in conceptos_pago:
            if any(palabra in pago['concepto'].lower() for palabra in ["ratificación", "certificado", "constancia", "duplicado", "convalidación", "subsanación", "cambio de turno", "traslado", "reincorporación", "licencia", "reserva"]):
                if "ratificación" in text_to_match and "ratificación" in pago['concepto'].lower(): precio = pago['monto']
                elif "reserva" in text_to_match and "reserva" in pago['concepto'].lower() and "reincorporación" not in pago['concepto'].lower(): precio = pago['monto']
                elif "licencia" in text_to_match and "licencia" in pago['concepto'].lower() and "reincorporación" not in pago['concepto'].lower(): precio = pago['monto']
                elif "certificado" in text_to_match and "certificado de estudios" in pago['concepto'].lower(): precio = pago['monto']
                elif "constancia" in text_to_match and "constancia de egresado" in pago['concepto'].lower(): precio = pago['monto']
                elif "duplicado" in text_to_match and "duplicado" in pago['concepto'].lower() and "recibo" not in pago['concepto'].lower(): precio = pago['monto']
                elif "convalidación" in text_to_match and "convalidación" in pago['concepto'].lower(): precio = pago['monto']
                elif "subsanación" in text_to_match and "subsanación" in pago['concepto'].lower(): precio = pago['monto']
                elif "cambio de turno" in text_to_match and "cambio de turno" in pago['concepto'].lower(): precio = pago['monto']
                elif "traslado interno" in text_to_match and "traslado interno" in pago['concepto'].lower(): precio = pago['monto']
                elif "traslado externo" in text_to_match and "traslado externo" in pago['concepto'].lower(): precio = pago['monto']

    # Fallback de emergencia (solo por si acaso)
    if not precio:
        if "ratificación" in cat + subcat: precio = 180.00
        elif "reserva" in cat + subcat: precio = 30.00
        elif "licencia" in cat + subcat: precio = 30.00
        elif "certificado" in cat + subcat: precio = 129.00
        elif "constancia" in cat + subcat: precio = 80.00
        elif "duplicado" in cat + subcat: precio = 245.00
        elif "convalidación" in cat + subcat: precio = 61.00
        elif "subsanación" in cat + subcat: precio = 40.00
        elif "cambio de turno" in cat + subcat: precio = 123.00
        elif "traslado interno" in cat + subcat: precio = 250.00
        elif "traslado externo" in cat + subcat: precio = 300.00

    info_extra = f"\n\nEl COSTO EXACTO es: S/ {precio:.2f}." if precio else "\n\nNo hay costo registrado para este trámite."
    contexto_str = json.dumps(contexto_json, indent=2, ensure_ascii=False)

    prompt_sistema = (
        f"Eres Tellito Bot, el asistente virtual del IESTP Julio César Tello. "
        f"Responde de forma amigable, persuasiva y clara. Usa emojis. "
        f"Aquí tienes la información oficial:\n\n{contexto_str}{info_extra}\n\n"
        f"REGLA DE ORO: SIEMPRE debes recordar al usuario:\n"
        f"1. Pagar en el Banco de la Nación (Cta. Cte. N° 00000-289078).\n"
        f"2. Llevar el voucher ORIGINAL y una COPIA a Caja.\n"
        f"Redacta un mensaje estructurado, bonito y directo."
    )
    
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": prompt_sistema}, {"role": "user", "content": prompt_usuario}],
        "temperature": 0.3
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        return response.json()['choices'][0]['message']['content']
    except Exception as e:
        print(f"Error IA: {e}")
        return "Tuve un pequeño problema pensando la respuesta, pero aquí tienes la info oficial."

# --- MENÚS DINÁMICOS ---
def get_menu_principal(perfil, primer_nombre, is_new=False):
    if is_new: menu = f"¡Todo listo, *{primer_nombre}*! 🚀 Ya tienes acceso al menú de *{perfil}*.\n\n"
    else: menu = f"¡Hola de nuevo, *{primer_nombre}*! 👋 ¿En qué te puedo ayudar hoy?\n\n"
    
    if perfil == "Prospecto":
        menu += "1️⃣  🎓 Modalidades de Admisión\n2️⃣  💰 Costos de Postular\n3️⃣  📄 Requisitos de Matrícula\n4️⃣  🏫 Nuestras Carreras\n5️⃣  🔄 Traslado Externo\n6️⃣  📍 Contacto y Ubicación\n7️⃣  💬 *Chatea con Tellito (IA)*\n\n"
    elif perfil == "Estudiante":
        menu += "1️⃣  📄 Trámites Académicos\n2️⃣  📑 Certificados y Constancias\n3️⃣  🎓 Ratificación, Reserva o Licencia\n4️⃣  🏫 Nuestro Instituto\n5️⃣  📍 Contacto y Ubicación\n6️⃣  🔍 Explorar otros perfiles\n7️⃣  💬 *Chatea con Tellito (IA)*\n\n"
    elif perfil == "Egresado":
        menu += "1️⃣  🎓 Titulación (TAP o Examen)\n2️⃣  📜 Constancia de Egresado\n3️⃣  📑 Certificación Modular\n4️⃣  🏫 Nuestro Instituto\n5️⃣  📍 Contacto y Ubicación\n6️⃣  🔍 Explorar otros perfiles\n7️⃣  💬 *Chatea con Tellito (IA)*\n\n"
        
    menu += "🔹 _Escribe el número de tu opción o hazme una pregunta directa._"
    return menu

@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    if params.get("hub.mode") == "subscribe" and params.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(params.get("hub.challenge"))
    return Response(status_code=403)

@app.post("/webhook")
async def recibir_mensaje(request: Request):
    body = await request.json()
    try:
        if body.get("object") == "whatsapp_business_account":
            value = body['entry'][0]['changes'][0]['value']
            if 'messages' not in value: return Response(content="OK", status_code=200)
            message = value['messages'][0]
            if 'from' not in message: return Response(content="OK", status_code=200)
                
            numero = message['from']
            if message.get('type') != 'text': return Response(content="OK", status_code=200)
            
            texto = message['text']['body'].strip()
            texto_lower = texto.lower()
            print(f"📩 {numero}: {texto}")
            
            estado = user_states.get(numero, "inicio")
            respuesta = ""
            enviar_texto_final = True

            # --- REGISTRO ---
            if estado == "inicio":
                cliente_existente = cargar_cliente(numero)
                if cliente_existente:
                    user_data[numero] = cliente_existente
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(cliente_existente['perfil'], cliente_existente['nombres'].split()[0], is_new=False)
                else:
                    user_states[numero] = "esperando_nombre"
                    user_data[numero] = {}
                    respuesta = "¡Hola! 👋 Qué gusto tenerte por aquí. Bienvenido al *IESTP Julio César Tello*. 🏫✨\n\nSoy *Tellito Bot*, tu asistente virtual. Para iniciar, ¿me podrías decir tus *Nombres y Apellidos*? 📝😊"

            elif estado == "esperando_nombre":
                user_data[numero]['nombres'] = texto.title()
                user_states[numero] = "esperando_perfil"
                respuesta = f"¡Un gusto, *{texto.split()[0]}*! 🤝\n\nCuéntame, ¿cuál es tu situación actual? (Responde con el número):\n\n1️⃣ 🎓 Soy *Prospecto* (quiero postular)\n2️⃣ 📚 Soy *Estudiante* actual\n3️⃣ 🎉 Soy *Egresado*"

            elif estado == "esperando_perfil":
                if texto_lower in ["1", "prospecto"]: perfil = "Prospecto"
                elif texto_lower in ["2", "estudiante"]: perfil = "Estudiante"
                elif texto_lower in ["3", "egresado"]: perfil = "Egresado"
                else: 
                    enviar_mensaje_meta(numero, "⚠️ Elige 1, 2 o 3.")
                    return Response(content="OK", status_code=200)
                
                user_data[numero]['perfil'] = perfil
                user_states[numero] = "esperando_carrera"
                pregunta = "¡Excelente! 🚀 Ahora dime, ¿cuál es la *carrera de tu interés*? (Responde con el número):" if perfil == "Prospecto" else "¡Excelente! 🎓 Ahora dime, ¿*a qué carrera perteneces*? (Responde con el número):"
                respuesta = f"{pregunta}\n\n1️⃣ 💼 Administración\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Sistemas\n4️⃣ ⚡ Electricidad\n5️⃣ 🚗 Mecánica Auto\n6️⃣ ⚙️ Mecánica Prod\n7️⃣ 📋 Asistencia"

            elif estado == "esperando_carrera":
                carreras = {"1": "Administración de Empresas", "2": "Contabilidad", "3": "Desarrollo de Sistemas", "4": "Electricidad Industrial", "5": "Mecánica Automotriz", "6": "Mecánica de Producción", "7": "Asistencia Administrativa"}
                if texto_lower in carreras: carrera_elegida = carreras[texto_lower]
                else: 
                    enviar_mensaje_meta(numero, "⚠️ Elige un número del 1 al 7.")
                    return Response(content="OK", status_code=200)
                
                user_data[numero]['carrera'] = carrera_elegida
                user_states[numero] = "menu_principal"
                guardar_cliente(numero, user_data[numero]['nombres'], user_data[numero]['perfil'], user_data[numero]['carrera'])
                respuesta = get_menu_principal(user_data[numero]['perfil'], user_data[numero]['nombres'].split()[0], is_new=True)

            # --- VOLVER ATRÁS (0) ---
            elif texto_lower in ["0", "volver", "atrás", "atras"]:
                if estado in ["ver_certificado", "ver_tramite", "ver_matricula", "ver_carrera"]:
                    # Volvemos al menú específico (Submenú)
                    if estado == "ver_certificado": user_states[numero] = "menu_certificados"; respuesta = "📑 *CERTIFICADOS*\n\n1️⃣ Certificado de Estudios\n2️⃣ Constancia de Egresado\n3️⃣ Duplicado de Título"
                    elif estado == "ver_tramite": user_states[numero] = "menu_tramites"; respuesta = "📄 *TRÁMITES*\n\n1️⃣ Cambio de Turno\n2️⃣ Subsanación\n3️⃣ Convalidación\n4️⃣ Traslado Interno"
                    elif estado == "ver_matricula": user_states[numero] = "menu_matricula_est"; respuesta = "🎓 *MATRÍCULA*\n\n1️⃣ Ratificación\n2️⃣ Reserva\n3️⃣ Licencia"
                    elif estado == "ver_carrera": user_states[numero] = "menu_carreras"; respuesta = "🏫 *CARRERAS*\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas de Información\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa"
                elif estado in ["explorar_prospecto", "explorar_estudiante", "explorar_egresado"]:
                    user_states[numero] = "menu_explorar"
                    perfil_actual = user_data[numero]['perfil']
                    if perfil_actual == "Prospecto": msg = "1️⃣ 📚 *Estudiante*\n2️⃣ 🎉 *Egresado*"
                    elif perfil_actual == "Estudiante": msg = "1️⃣ 🎓 *Prospecto*\n2️⃣ 🎉 *Egresado*"
                    else: msg = "1️⃣ 🎓 *Prospecto*\n2️⃣ 📚 *Estudiante*"
                    respuesta = f"🔍 *MODO EXPLORADOR*\n\n¿Qué información te gustaría ver?\n\n{msg}"
                elif estado == "menu_explorar" or estado == "modo_ia":
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(user_data[numero]['perfil'], user_data[numero]['nombres'].split()[0], is_new=False)
                elif estado in ["menu_tramites", "menu_certificados", "menu_matricula_est", "menu_carreras"]:
                    # Si estamos en el submenú, 0 nos lleva al menú principal
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(user_data[numero]['perfil'], user_data[numero]['nombres'].split()[0], is_new=False)
                else:
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(user_data[numero]['perfil'], user_data[numero]['nombres'].split()[0], is_new=False)

            # --- MENÚ PRINCIPAL ---
            elif any(palabra in texto_lower for palabra in ["hola", "menu", "menú", "inicio", "comenzar", "empezar"]):
                user_states[numero] = "menu_principal"
                respuesta = get_menu_principal(user_data[numero]['perfil'], user_data[numero]['nombres'].split()[0], is_new=False)

            elif estado == "menu_principal":
                perfil = user_data[numero]['perfil']
                
                if texto_lower == "7":
                    user_states[numero] = "modo_ia"
                    respuesta = "💬 *¡Modo IA Activado!* Pregúntame cualquier cosa sobre el instituto y te responderé al instante. ¿Qué necesitas saber?"
                
                elif perfil == "Prospecto":
                    if texto_lower == "1": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame las modalidades de admisión", buscar_faq("Modalidades"))
                    elif texto_lower == "2": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame los costos de postular", buscar_faq("Costo"))
                    elif texto_lower == "3": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame los requisitos de matrícula", buscar_faq("Documentos"))
                    elif texto_lower == "4": user_states[numero] = "menu_carreras"; respuesta = "🏫 *CARRERAS*\n\n1️⃣ 💼 Administración\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Sistemas\n4️⃣ ⚡ Electricidad\n5️⃣ 🚗 Mecánica Auto\n6️⃣ ⚙️ Mecánica Prod\n7️⃣ 📋 Asistencia"
                    elif texto_lower == "5": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame el traslado externo", buscar_faq("Traslado externo"))
                    elif texto_lower == "6": respuesta = "🏢 *Atención Personalizada*\n\n📍 Visítanos en: Mz. \"A\", Lote 1, 3er Sector, Grupo 2, Villa El Salvador.\n🕒 Horario: L-V de 8:00 a 13:00 y 14:30 a 21:00.\n\n¡Estamos para ayudarte! 😊"
                    else:
                        res_json = buscar_faq(texto)
                        if res_json: respuesta = redactar_con_ia(texto, res_json)
                        else: enviar_fallback_ciclico(numero); enviar_texto_final = False

                elif perfil == "Estudiante":
                    if texto_lower == "1": user_states[numero] = "menu_tramites"; respuesta = "📄 *TRÁMITES*\n\n1️⃣ Cambio de Turno\n2️⃣ Subsanación\n3️⃣ Convalidación\n4️⃣ Traslado Interno"
                    elif texto_lower == "2": user_states[numero] = "menu_certificados"; respuesta = "📑 *CERTIFICADOS*\n\n1️⃣ Certificado de Estudios\n2️⃣ Constancia de Egresado\n3️⃣ Duplicado de Título"
                    elif texto_lower == "3": user_states[numero] = "menu_matricula_est"; respuesta = "🎓 *MATRÍCULA*\n\n1️⃣ Ratificación\n2️⃣ Reserva\n3️⃣ Licencia"
                    elif texto_lower == "4": user_states[numero] = "menu_carreras"; respuesta = "🏫 *CARRERAS*\n\n1️⃣ 💼 Administración\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Sistemas\n4️⃣ ⚡ Electricidad\n5️⃣ 🚗 Mecánica Auto\n6️⃣ ⚙️ Mecánica Prod\n7️⃣ 📋 Asistencia"
                    elif texto_lower == "5": respuesta = "🏢 *Atención Personalizada*\n\n📍 Visítanos en: Mz. \"A\", Lote 1, 3er Sector, Grupo 2, Villa El Salvador.\n🕒 Horario: L-V de 8:00 a 13:00 y 14:30 a 21:00.\n\n¡Estamos para ayudarte! 😊"
                    elif texto_lower == "6": user_states[numero] = "menu_explorar"; respuesta = "🔍 *MODO EXPLORADOR*\n\n¿Qué información te gustaría ver?\n\n1️⃣ Ver perfil de Prospecto\n2️⃣ Ver perfil de Egresado"
                    else:
                        res_json = buscar_faq(texto)
                        if res_json: respuesta = redactar_con_ia(texto, res_json)
                        else: enviar_fallback_ciclico(numero); enviar_texto_final = False

                elif perfil == "Egresado":
                    if texto_lower == "1": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame las modalidades de titulación", buscar_faq("Modalidades", "Titulación"))
                    elif texto_lower == "2": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame la constancia de egresado", buscar_faq("¿Qué es?", "Constancia"))
                    elif texto_lower == "3": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame la certificación modular", buscar_faq("¿Qué es?", "Certificación"))
                    elif texto_lower == "4": user_states[numero] = "menu_carreras"; respuesta = "🏫 *CARRERAS*\n\n1️⃣ 💼 Administración\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Sistemas\n4️⃣ ⚡ Electricidad\n5️⃣ 🚗 Mecánica Auto\n6️⃣ ⚙️ Mecánica Prod\n7️⃣ 📋 Asistencia"
                    elif texto_lower == "5": respuesta = "🏢 *Atención Personalizada*\n\n📍 Visítanos en: Mz. \"A\", Lote 1, 3er Sector, Grupo 2, Villa El Salvador.\n🕒 Horario: L-V de 8:00 a 13:00 y 14:30 a 21:00.\n\n¡Estamos para ayudarte! 😊"
                    elif texto_lower == "6": user_states[numero] = "menu_explorar"; respuesta = "🔍 *MODO EXPLORADOR*\n\n¿Qué información te gustaría ver?\n\n1️⃣ Ver perfil de Prospecto\n2️⃣ Ver perfil de Estudiante"
                    else:
                        res_json = buscar_faq(texto)
                        if res_json: respuesta = redactar_con_ia(texto, res_json)
                        else: enviar_fallback_ciclico(numero); enviar_texto_final = False

            # --- MODO IA LIBRE ---
            elif estado == "modo_ia":
                if texto_lower in ["menú", "menu", "0", "salir"]:
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(user_data[numero]['perfil'], user_data[numero]['nombres'].split()[0], is_new=False)
                else:
                    contexto = buscar_faq(texto)
                    if not contexto: contexto = "El usuario pregunta algo que no está en el JSON. Si no lo sabes, derívalo a Secretaría Académica."
                    respuesta = redactar_con_ia(texto, contexto)

            # --- SUBMENÚS (Redactados por la IA) ---
            elif estado == "menu_tramites":
                if texto_lower == "1": respuesta = redactar_con_ia("Explícame el cambio de turno", buscar_faq("Cambio de Turno")); user_states[numero] = "ver_tramite"
                elif texto_lower == "2": respuesta = redactar_con_ia("Explícame la subsanación", buscar_faq("Subsanación")); user_states[numero] = "ver_tramite"
                elif texto_lower == "3": respuesta = redactar_con_ia("Explícame la convalidación", buscar_faq("Convalidación")); user_states[numero] = "ver_tramite"
                elif texto_lower == "4": respuesta = redactar_con_ia("Explícame el traslado interno", buscar_faq("Traslado interno")); user_states[numero] = "ver_tramite"
                else: respuesta = "⚠️ Elige una opción válida."

            elif estado == "menu_certificados":
                if texto_lower == "1": respuesta = redactar_con_ia("Explícame el certificado de estudios", buscar_faq("Certificado de estudios")); user_states[numero] = "ver_certificado"
                elif texto_lower == "2": respuesta = redactar_con_ia("Explícame la constancia de egresado", buscar_faq("¿Qué es?", "Constancia de egresado")); user_states[numero] = "ver_certificado"
                elif texto_lower == "3": respuesta = redactar_con_ia("Explícame el duplicado de título", buscar_faq("Duplicado")); user_states[numero] = "ver_certificado"
                else: respuesta = "⚠️ Elige una opción válida."

            elif estado == "menu_matricula_est":
                if texto_lower == "1": respuesta = redactar_con_ia("Explícame la ratificación de matrícula", buscar_faq("Ratificación")); user_states[numero] = "ver_matricula"
                elif texto_lower == "2": respuesta = redactar_con_ia("Explícame la reserva de matrícula", buscar_faq("Reserva")); user_states[numero] = "ver_matricula"
                elif texto_lower == "3": respuesta = redactar_con_ia("Explícame la licencia de estudios", buscar_faq("Licencia")); user_states[numero] = "ver_matricula"
                else: respuesta = "⚠️ Elige una opción válida."

            # --- MENÚ DE CARRERAS ---
            elif estado == "menu_carreras":
                imagenes = {
                    "1": {"url": "https://i.ibb.co/fz7kxPqn/administraci-n.jpg", "texto": "Administración de Empresas: Lidera y emprende."},
                    "2": {"url": "https://i.ibb.co/Jwh71wk4/Contabilidad.jpg", "texto": "Contabilidad: El lenguaje de los negocios."},
                    "3": {"url": "https://i.ibb.co/N2WGV9Gy/Desarrollo-de-Sistemas.jpg", "texto": "Desarrollo de Sistemas: Construye el futuro."}, 
                    "4": {"url": "https://i.ibb.co/yn7ph2S0/electricidad.jpg", "texto": "Electricidad Industrial: Energía y futuro."}, 
                    "5": {"url": "https://i.ibb.co/20z3NB0F/mec-nica-automotriz.jpg", "texto": "Mecánica Automotriz: Domina la tecnología."},
                    "6": {"url": "https://i.ibb.co/39dPy4pq/producci-n.jpg", "texto": "Mecánica de Producción: Transforma la industria."},
                    "7": {"url": "https://i.ibb.co/hRCrbRFp/Asistencia-Administrativa.jpg", "texto": "Asistencia Administrativa: El pilar de la organización."}
                }
                if texto_lower in imagenes:
                    info = imagenes[texto_lower]
                    enviar_imagen_meta(numero, info['url'], info['texto'])
                    respuesta = "📌 ¿Ver otra? Escribe el número o *menú* para volver."
                else:
                    enviar_fallback_ciclico(numero); enviar_texto_final = False

            else:
                res_json = buscar_faq(texto)
                if res_json: respuesta = redactar_con_ia(texto, res_json)
                else: enviar_fallback_ciclico(numero); enviar_texto_final = False

            # --- ENVÍO FINAL Y LIMPIEZA DE FOOTERS ---
            if enviar_texto_final:
                if not respuesta: 
                    enviar_fallback_ciclico(numero)
                else:
                    # Limpiamos footers viejos para no duplicar
                    respuesta = respuesta.replace("\n\n🔹 _Escribe *menú*", "").replace("\n\n🔹 _Escribe *0*", "").replace("\n\n🔹 _Escribe el número de tu opción._", "")

                    if estado in ["menu_tramites", "menu_certificados", "menu_matricula_est", "menu_carreras", "menu_explorar"]:
                        respuesta += "\n\n🔹 _Escribe el número de tu opción._"
                    elif estado in ["ver_certificado", "ver_tramite", "ver_matricula", "ver_carrera", "explorar_prospecto", "explorar_estudiante", "explorar_egresado"]:
                        respuesta += "\n\n🔹 _Escribe *0* para volver atrás._\n🔹 _Escribe *menú* para ir al menú principal._"
                    elif estado == "modo_ia":
                        respuesta += "\n\n🔹 _Escribe *0* o *menú* para salir del modo IA._"
                    # Menu principal ya tiene su propio footer en la función get_menu_principal

                    enviar_mensaje_meta(numero, respuesta)

    except Exception as e:
        print(f"Error en el flujo: {e}")
    return Response(content="OK", status_code=200)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)