import os
import json
import requests
from datetime import datetime
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from supabase import create_client

# 1. Cargar variables de entorno
load_dotenv()

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

@app.get("/")
def read_root():
    return {"status": "Tellito Bot está despierto y funcionando!"}

# 2. Cargar JSON
with open('base_conocimiento.json', 'r', encoding='utf-8') as f:
    data = json.load(f)
faqs = data['faqs']
conceptos_pago = data['conceptos_pago']
carreras_info = data['info_carreras']

user_states = {}
user_data = {}

# --- PÓSTERS DE FALLBACK ---
POSTERS_FALLBACK = [
    {"url": "https://i.ibb.co/7tJZHVc2/1.png", "texto": "🤔 ¿Tienes alguna duda académica? ¡No busques más! Estoy aquí para responderte al instante. Escribe *menú* para empezar. 🚀"},
    {"url": "https://i.ibb.co/zhbXXXsX/2.png", "texto": "🥤 ¡La bebida oficial de la información! Refréscate con respuestas inmediatas sobre el instituto, las 24 horas del día. Escribe *menú* para tomarte una. 😉"},
    {"url": "https://i.ibb.co/LXJDyTQd/3.png", "texto": "🛡️ ¡No salgas a la superficie a hacer filas! Aquí estoy seguro, 24/7. Escribe *menú* para que te ayude con cualquier trámite. 🤖✨"}
]

# --- CARTERA DE CLIENTES ---
if not os.path.exists('logs'): os.makedirs('logs')

def guardar_cliente(numero, nombres, perfil, carrera):
    try:
        response = supabase.table('clientes').update({
            "nombres": nombres, "perfil": perfil, "carrera": carrera
        }).eq('whatsapp', numero).execute()
        
        if not response.data:
            supabase.table('clientes').insert({
                "whatsapp": numero, "nombres": nombres, "perfil": perfil, "carrera": carrera
            }).execute()
        print(f"✅ Cliente guardado en Supabase: {nombres}")
    except Exception as e:
        print(f"❌ Error guardando en BD: {e}")

def cargar_cliente(numero):
    try:
        response = supabase.table('clientes').select('*').eq('whatsapp', numero).execute()
        if response.data:
            print(f"✅ Cliente encontrado en Supabase: {response.data[0]['nombres']}")
            return {
                "nombres": response.data[0]['nombres'],
                "perfil": response.data[0]['perfil'],
                "carrera": response.data[0]['carrera']
            }
    except Exception as e:
        print(f"❌ Error cargando de BD: {e}")
    return None

# --- ENVÍO DE MENSAJES ---
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

# --- BÚSQUEDA ---
def buscar_faq(termino, categoria=None):
    termino_lower = termino.lower()
    for f in faqs:
        if termino_lower in f['subcategoria'].lower():
            if categoria:
                if categoria.lower() in f['categoria'].lower(): return f
            else: return f
        if termino_lower in f['categoria'].lower(): return f
        if any(termino_lower in p.lower() for p in f.get('preguntas', [])):
            return f
    return None

def get_faq_by_id(faq_id):
    for f in faqs:
        if f.get('id') == faq_id:
            return f
    return None

# --- IA DEEPSEEK ---
def redactar_con_ia(prompt_usuario, contexto_json):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    
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
        
        if not precio:
            if "ratificación" in text_to_match: precio = 180.00
            elif "reserva" in text_to_match: precio = 30.00
            elif "licencia" in text_to_match: precio = 30.00
            elif "certificado" in text_to_match: precio = 129.00
            elif "constancia" in text_to_match: precio = 80.00
            elif "duplicado" in text_to_match: precio = 245.00
            elif "convalidación" in text_to_match: precio = 61.00
            elif "subsanación" in text_to_match: precio = 40.00
            elif "cambio de turno" in text_to_match: precio = 123.00
            elif "traslado interno" in text_to_match: precio = 250.00
            elif "traslado externo" in text_to_match: precio = 300.00

    info_extra = f"\n\nEl COSTO EXACTO es: S/ {precio:.2f}." if precio else "\n\nNo hay costo registrado para este trámite."
    contexto_str = json.dumps(contexto_json, indent=2, ensure_ascii=False) if isinstance(contexto_json, dict) else str(contexto_json)

    prompt_sistema = (
        f"Eres Tellito Bot, el asistente virtual del IESTP Julio César Tello. "
        f"Tu única tarea es redactar la información del trámite de forma amigable, clara y CONCISA. Usa emojis. "
        f"Aquí tienes la información oficial:\n\n{contexto_str}{info_extra}\n\n"
        f"REGLA DE ORO: SIEMPRE debes recordar al usuario:\n"
        f"1. Pagar en el Banco de la Nación (Cta. Cte. N° 00000-289078).\n"
        f"2. Llevar el voucher ORIGINAL y una COPIA a Caja.\n"
        f"REGLA DE PRECIOS: Si el usuario pregunta por el costo del examen de admisión, el monto total es S/ 180.00 (S/ 170.00 de inscripción + S/ 10.00 de prospecto).\n"
        f"REGLAS DE FORMATO ESTRICTAS (ESTÁS EN WHATSAPP):\n"
        f"- Usa SOLO un asterisco a cada lado para negrita: *texto*\n"
        f"- Usa guion bajo a cada lado para cursiva: _texto_\n"
        f"- NO uses almohadillas (#) ni doble asterisco (**).\n"
        f"- ¡PROHIBIDO! NO agregues menús de opciones (1, 2, 3...). NO agregues textos como 'Escribe menú' o 'Escribe 0'. Solo entrega el texto informativo."
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

# --- MENÚS ---
def get_menu_principal(perfil, primer_nombre, is_new=False):
    if is_new: menu = f"¡Todo listo, *{primer_nombre}*! 🚀 Ya tienes acceso al menú de *{perfil}*.\n\n"
    else: menu = f"¡Hola de nuevo, *{primer_nombre}*! 👋 ¿En qué te puedo ayudar hoy?\n\n"
    
    if perfil == "Prospecto":
        menu += "1️⃣  🎓 Modalidades de Admisión\n2️⃣  💰 Costos de Postular\n3️⃣  📄 Requisitos de Matrícula\n4️⃣  🏫 Nuestras Carreras\n5️⃣  🔄 Traslado Externo\n6️⃣  📍 Contacto y Ubicación\n7️⃣  💬 *Chatea con Tellito (IA)*\n\n"
    elif perfil == "Estudiante":
        menu += "1️⃣  📄 Trámites Académicos\n2️⃣  📑 Certificados y Constancias\n3️⃣  🎓 Ratificación, Reserva o Licencia\n4️⃣  🏫 Nuestro Instituto\n5️⃣  📍 Contacto y Ubicación\n6️⃣  🔍 Explorar otros perfiles\n7️⃣  💬 *Chatea con Tellito (IA)*\n\n"
    elif perfil == "Egresado":
        menu += "1️⃣  🎓 Titulación (TAP o Examen)\n2️⃣  📜 Constancia de Egresado\n3️⃣  📑 Certificación Modular\n4️⃣  🏫 Nuestro Instituto\n5️⃣  📍 Contacto y Ubicación\n6️⃣  🔍 Explorar otros perfiles\n7️⃣  💬 *Chatea con Tellito (IA)*\n\n"
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

            # 🚨 ESCUDO ANTI-VISTO
            if texto_lower in ["cancelar", "reiniciar", "salir", "menu", "menú"]:
                if estado in ["esperando_nombre", "esperando_perfil", "esperando_carrera"]:
                    user_states[numero] = "esperando_nombre"
                    user_data[numero] = {}
                    enviar_mensaje_meta(numero, "🔄 Entendido. He reiniciado el registro. ¡Hola de nuevo! 👋 ¿Me podrías decir tus *Nombres y Apellidos*? 📝😊")
                    return Response(content="OK", status_code=200)
                    
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
                if texto_lower in ["cancelar", "menu", "menú", "reiniciar"]:
                    user_states[numero] = "inicio"
                    respuesta = "Entendido. He reiniciado el registro. ¡Hola de nuevo! 👋 ¿Me podrías decir tus *Nombres y Apellidos*? 📝"
                else:
                    user_data[numero]['nombres'] = texto.title()
                    user_states[numero] = "esperando_perfil"
                    respuesta = f"¡Un gusto, *{texto.split()[0]}*! 🤝\n\nCuéntame, ¿cuál es tu situación actual? (Responde con el número):\n\n1️⃣ 🎓 Soy *Prospecto* (quiero postular)\n2️⃣ 📚 Soy *Estudiante* actual\n3️⃣ 🎉 Soy *Egresado*"

            elif estado == "esperando_perfil":
                if texto_lower in ["cancelar", "menu", "menú", "reiniciar"]:
                    user_states[numero] = "inicio"
                    respuesta = "Entendido. He reiniciado el registro. ¡Hola de nuevo! 👋 ¿Me podrías decir tus *Nombres y Apellidos*? 📝"
                else:
                    if texto_lower in ["1", "prospecto"]: perfil = "Prospecto"
                    elif texto_lower in ["2", "estudiante"]: perfil = "Estudiante"
                    elif texto_lower in ["3", "egresado"]: perfil = "Egresado"
                    else: 
                        enviar_mensaje_meta(numero, "⚠️ Elige 1, 2 o 3 (o escribe 'cancelar' para reiniciar).")
                        return Response(content="OK", status_code=200)
                    
                    user_data[numero]['perfil'] = perfil
                    user_states[numero] = "esperando_carrera"
                    pregunta = "¡Excelente! 🚀 Ahora dime, ¿cuál es la *carrera de tu interés*? (Responde con el número):" if perfil == "Prospecto" else "¡Excelente! 🎓 Ahora dime, ¿*a qué carrera perteneces*? (Responde con el número):"
                    respuesta = f"{pregunta}\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas de Información\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa"

            elif estado == "esperando_carrera":
                if texto_lower in ["cancelar", "menu", "menú", "reiniciar"]:
                    user_states[numero] = "inicio"
                    respuesta = "Entendido. He reiniciado el registro. ¡Hola de nuevo! 👋 ¿Me podrías decir tus *Nombres y Apellidos*? 📝"
                else:
                    carreras = {"1": "Administración de Empresas", "2": "Contabilidad", "3": "Desarrollo de Sistemas", "4": "Electricidad Industrial", "5": "Mecánica Automotriz", "6": "Mecánica de Producción", "7": "Asistencia Administrativa"}
                    if texto_lower in carreras: carrera_elegida = carreras[texto_lower]
                    else: 
                        enviar_mensaje_meta(numero, "⚠️ Elige un número del 1 al 7 (o escribe 'cancelar' para reiniciar).")
                        return Response(content="OK", status_code=200)
                    
                    user_data[numero]['carrera'] = carrera_elegida
                    user_states[numero] = "menu_principal"
                    guardar_cliente(numero, user_data[numero]['nombres'], user_data[numero]['perfil'], user_data[numero]['carrera'])
                    respuesta = get_menu_principal(user_data[numero]['perfil'], user_data[numero]['nombres'].split()[0], is_new=True)

            # --- VOLVER ATRÁS (0) ---
            elif texto_lower in ["0", "volver", "atrás", "atras"]:
                if estado in ["ver_certificado", "ver_tramite", "ver_matricula", "ver_carrera"]:
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
                    if texto_lower == "1": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame las modalidades de admisión", get_faq_by_id("FAQ-014"))
                    elif texto_lower == "2": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame los costos de postular", get_faq_by_id("FAQ-017"))
                    elif texto_lower == "3": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame los requisitos de matrícula", get_faq_by_id("FAQ-029"))
                    elif texto_lower == "4": user_states[numero] = "menu_carreras"; respuesta = "🏫 *CARRERAS*\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas de Información\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa"
                    elif texto_lower == "5": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame el traslado externo", get_faq_by_id("FAQ-074"))
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
                    if texto_lower == "1": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame las modalidades de titulación", get_faq_by_id("FAQ-092"))
                    elif texto_lower == "2": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame la constancia de egresado", get_faq_by_id("FAQ-086"))
                    elif texto_lower == "3": user_states[numero] = "ver_tramite"; respuesta = redactar_con_ia("Explícame la certificación modular", get_faq_by_id("FAQ-110"))
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
                    if not contexto:
                        contexto = {"info_faqs": faqs, "info_precios": conceptos_pago}
                    respuesta = redactar_con_ia(texto, contexto)

            # --- SUBMENÚS ---
            elif estado == "menu_tramites":
                if texto_lower == "1": respuesta = redactar_con_ia("Explícame el cambio de turno", get_faq_by_id("FAQ-117")); user_states[numero] = "ver_tramite"
                elif texto_lower == "2": respuesta = redactar_con_ia("Explícame la subsanación", get_faq_by_id("FAQ-118")); user_states[numero] = "ver_tramite"
                elif texto_lower == "3": respuesta = redactar_con_ia("Explícame la convalidación", get_faq_by_id("FAQ-062")); user_states[numero] = "ver_tramite"
                elif texto_lower == "4": respuesta = redactar_con_ia("Explícame el traslado interno", get_faq_by_id("FAQ-068")); user_states[numero] = "ver_tramite"
                else: respuesta = "⚠️ Elige una opción válida."

            elif estado == "menu_certificados":
                if texto_lower == "1": respuesta = redactar_con_ia("Explícame el certificado de estudios", get_faq_by_id("FAQ-080")); user_states[numero] = "ver_certificado"
                elif texto_lower == "2": respuesta = redactar_con_ia("Explícame la constancia de egresado", get_faq_by_id("FAQ-086")); user_states[numero] = "ver_certificado"
                elif texto_lower == "3": respuesta = redactar_con_ia("Explícame el duplicado de título", get_faq_by_id("FAQ-105")); user_states[numero] = "ver_certificado"
                else: respuesta = "⚠️ Elige una opción válida."

            elif estado == "menu_matricula_est":
                if texto_lower == "1": respuesta = redactar_con_ia("Explícame la ratificación de matrícula", get_faq_by_id("FAQ-037")); user_states[numero] = "ver_matricula"
                elif texto_lower == "2": respuesta = redactar_con_ia("Explícame la reserva de matrícula", get_faq_by_id("FAQ-042")); user_states[numero] = "ver_matricula"
                elif texto_lower == "3": respuesta = redactar_con_ia("Explícame la licencia de estudios", get_faq_by_id("FAQ-050")); user_states[numero] = "ver_matricula"
                else: respuesta = "⚠️ Elige una opción válida."

            # --- MENÚ DE CARRERAS ---
            elif estado == "menu_carreras":
                if texto_lower in carreras_info:
                    info = carreras_info[texto_lower]
                    # 1. Enviamos la imagen con un título corto
                    enviar_imagen_meta(numero, info['url'], info['titulo'])
                    # 2. Enviamos el texto largo como un mensaje aparte
                    enviar_mensaje_meta(numero, info['texto'])
                    # 3. Mensaje final de opciones
                    respuesta = "📌 ¿Ver otra carrera? Escribe el número o *menú* para volver."
                else:
                    enviar_fallback_ciclico(numero); enviar_texto_final = False

            # --- ENVÍO FINAL ---
            if enviar_texto_final:
                if not respuesta: 
                    enviar_fallback_ciclico(numero)
                else:
                    estado_actual = user_states.get(numero, "menu_principal")

                    if estado_actual in ["menu_tramites", "menu_certificados", "menu_matricula_est", "menu_carreras", "menu_explorar"]:
                        respuesta += "\n\n🔹 _Escribe el número de tu opción._\n🔹 _Escribe *menú* para ir al menú principal._"
                    
                    elif estado_actual in ["ver_certificado", "ver_tramite", "ver_matricula", "ver_carrera", "explorar_prospecto", "explorar_estudiante", "explorar_egresado"]:
                        respuesta += "\n\n🔹 _Escribe *0* para volver atrás._\n🔹 _Escribe *menú* para ir al menú principal._"
                    
                    elif estado_actual == "modo_ia":
                        respuesta += "\n\n🔹 _Escribe tu pregunta o escribe *0* para salir._"
                    
                    elif estado_actual == "menu_principal":
                        respuesta += "\n\n🔹 _Escribe el número de tu opción._"

                    enviar_mensaje_meta(numero, respuesta)

    except Exception as e:
        print(f"Error en el flujo: {e}")
    return Response(content="OK", status_code=200)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
