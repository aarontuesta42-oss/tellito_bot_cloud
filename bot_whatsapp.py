import os
import json
import requests
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
carreras_info = data.get('info_carreras', {})

user_states = {}
user_data = {}

# --- PÓSTERS DE FALLBACK ---
POSTERS_FALLBACK = [
    {"url": "https://i.ibb.co/7tJZHVc2/1.png", "texto": "🤔 ¿Tienes alguna duda académica? ¡No busques más! Estoy aquí para responderte al instante. Escribe *menú* para empezar. 🚀"},
    {"url": "https://i.ibb.co/zhbXXXsX/2.png", "texto": "🥤 ¡La bebida oficial de la información! Refréscate con respuestas inmediatas sobre el instituto, las 24 horas del día. Escribe *menú* para tomarte una. 😉"},
    {"url": "https://i.ibb.co/LXJDyTQd/3.png", "texto": "🛡️ ¡No salgas a la superficie a hacer filas! Aquí estoy seguro, 24/7. Escribe *menú* para que te ayude con cualquier trámite. 🤖✨"}
]

# --- BASE DE DATOS ---
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
        print(f"✅ Cliente guardado: {nombres}")
    except Exception as e:
        print(f"❌ Error BD: {e}")

def cargar_cliente(numero):
    try:
        response = supabase.table('clientes').select('*').eq('whatsapp', numero).execute()
        if response.data:
            return {
                "nombres": response.data[0]['nombres'],
                "perfil": response.data[0]['perfil'],
                "carrera": response.data[0]['carrera']
            }
    except Exception as e:
        print(f"❌ Error BD: {e}")
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

# --- BÚSQUEDA DE FAQ ---
def get_faq_by_id(faq_id):
    for f in faqs:
        if f.get('id') == faq_id:
            return f
    return None

# --- IA DEEPSEEK (SOLO REDACTA) ---
def redactar_con_ia(prompt_usuario, faq_dict):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    
    contexto_str = json.dumps(faq_dict, indent=2, ensure_ascii=False)

    prompt_sistema = (
        f"Eres Tellito Bot, el asistente virtual del IESTP Julio César Tello. "
        f"Tu única tarea es redactar la información del trámite de forma amigable, clara y CONCISA. Usa emojis. "
        f"Aquí tienes la información oficial:\n\n{contexto_str}\n\n"
        f"REGLA DE ORO: SIEMPRE debes recordar al usuario:\n"
        f"1. Pagar en el Banco de la Nación (Cta. Cte. N° 00000-289078).\n"
        f"2. Llevar el voucher ORIGINAL y una COPIA a Caja.\n"
        f"REGLAS DE FORMATO ESTRICTAS (ESTÁS EN WHATSAPP):\n"
        f"- Usa SOLO un asterisco a cada lado para negrita: *texto*\n"
        f"- Usa guion bajo a cada lado para cursiva: _texto_\n"
        f"- NO uses almohadillas (#) ni doble asterisco (**).\n"
        f"- ¡PROHIBIDO! NO agregues menús de opciones (1, 2, 3...). NO hagas preguntas de seguimiento. Solo entrega la información."
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

# --- TEXTOS DE MENÚS ---
def get_menu_principal(primer_nombre, is_new=False):
    saludo = "¡Todo listo! 🚀 Ya estás registrado." if is_new else f"¡Hola de nuevo, *{primer_nombre}*! 👋"
    return f"{saludo}\nAquí tienes nuestro índice de consultas:\n\n" \
           "🎓 *INFORMACIÓN DE PROSPECTO*\n" \
           "1️⃣ Modalidades de Admisión\n2️⃣ Costos de Postular\n3️⃣ Requisitos de Matrícula\n4️⃣ Traslado Externo\n\n" \
           "📚 *INFORMACIÓN DE ESTUDIANTE*\n" \
           "5️⃣ Trámites Académicos\n6️⃣ Certificados y Constancias\n7️⃣ Ratificación, Reserva o Licencia\n\n" \
           "🎉 *INFORMACIÓN DE EGRESADO*\n" \
           "8️⃣ Titulación (TAP o Examen)\n9️⃣ Constancia de Egresado\n🔟 Certificación Modular\n\n" \
           "🏫 *GENERAL*\n" \
           "1️⃣1️⃣ Nuestras Carreras\n1️⃣2️⃣ Contacto y Ubicación\n\n" \
           "0️⃣ Reiniciar registro\n\n" \
           "🔹 _Escribe el número de tu opción._"

SUBMENUS = {
    "submenu_1": "🎓 *MODALIDADES DE ADMISIÓN*\n\n1️⃣ Ingreso Ordinario\n2️⃣ Ingreso por Exoneración\n3️⃣ Ingreso Extraordinario\n\n0️⃣ Volver al menú principal",
    "submenu_2": "💰 *COSTOS DE POSTULAR*\n\n1️⃣ Costo de Inscripción y Prospecto\n2️⃣ Costo de Matrícula\n\n0️⃣ Volver al menú principal",
    "submenu_3": "📄 *REQUISITOS DE MATRÍCULA*\n\n1️⃣ Documentos para Matrícula\n\n0️⃣ Volver al menú principal",
    "submenu_4": "🔄 *TRASLADO EXTERNO*\n\n1️⃣ Requisitos para Traslado Externo\n\n0️⃣ Volver al menú principal",
    "submenu_5": "📄 *TRÁMITES ACADÉMICOS*\n\n1️⃣ Cambio de Turno\n2️⃣ Subsanación de Cursos\n3️⃣ Convalidación de Cursos\n\n0️⃣ Volver al menú principal",
    "submenu_6": "📑 *CERTIFICADOS Y CONSTANCIAS*\n\n1️⃣ Certificado de Estudios\n2️⃣ Constancia de Egresado\n3️⃣ Duplicado de Título\n\n0️⃣ Volver al menú principal",
    "submenu_7": "🎓 *MATRÍCULA*\n\n1️⃣ Ratificación de Matrícula\n2️⃣ Reserva de Matrícula\n3️⃣ Licencia de Estudios\n\n0️⃣ Volver al menú principal",
    "submenu_8": "🎉 *TITULACIÓN*\n\n1️⃣ Trabajo de Aplicación Profesional (TAP)\n2️⃣ Examen de Suficiencia Profesional\n3️⃣ Requisitos para Titularse\n\n0️⃣ Volver al menú principal",
    "submenu_9": "📜 *CONSTANCIA DE EGRESADO*\n\n1️⃣ ¿Qué es y cómo obtenerla?\n\n0️⃣ Volver al menú principal",
    "submenu_10": "🔟 *CERTIFICACIÓN MODULAR*\n\n1️⃣ ¿Qué es y cómo obtenerla?\n\n0️⃣ Volver al menú principal",
    "menu_carreras": "🏫 *NUESTRAS CARRERAS*\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa\n\n0️⃣ Volver al menú principal",
}

# MAPEO: (Submenú, Opción elegida) -> ID del JSON
MAPEO_FAQS = {
    "submenu_1": {"1": "FAQ-015", "2": "FAQ-016", "3": "FAQ-023"},
    "submenu_2": {"1": "FAQ-017", "2": "FAQ-036"}, # Costo examen, Costo matrícula
    "submenu_3": {"1": "FAQ-029"},
    "submenu_4": {"1": "FAQ-074"},
    "submenu_5": {"1": "FAQ-117", "2": "FAQ-118", "3": "FAQ-062"},
    "submenu_6": {"1": "FAQ-080", "2": "FAQ-086", "3": "FAQ-105"},
    "submenu_7": {"1": "FAQ-037", "2": "FAQ-042", "3": "FAQ-050"},
    "submenu_8": {"1": "FAQ-093", "2": "FAQ-094", "3": "FAQ-096"},
    "submenu_9": {"1": "FAQ-086"},
    "submenu_10": {"1": "FAQ-110"},
}

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
                cliente = cargar_cliente(numero)
                if cliente:
                    user_data[numero] = cliente
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(cliente['nombres'].split()[0], is_new=False)
                else:
                    user_states[numero] = "esperando_nombre"
                    user_data[numero] = {}
                    respuesta = "¡Hola! 👋 Bienvenido al *IESTP Julio César Tello*. 🏫✨\n\nSoy *Tellito Bot*. Para iniciar, ¿me podrías decir tus *Nombres y Apellidos*? 📝😊"

            elif estado == "esperando_nombre":
                user_data[numero]['nombres'] = texto.title()
                user_states[numero] = "esperando_perfil"
                respuesta = f"¡Un gusto, *{texto.split()[0]}*! 🤝\n\n¿Cuál es tu situación actual?\n\n1️⃣ 🎓 Soy *Prospecto*\n2️⃣ 📚 Soy *Estudiante*\n3️⃣ 🎉 Soy *Egresado*"

            elif estado == "esperando_perfil":
                perfiles = {"1": "Prospecto", "2": "Estudiante", "3": "Egresado"}
                if texto_lower in perfiles:
                    user_data[numero]['perfil'] = perfiles[texto_lower]
                    user_states[numero] = "esperando_carrera"
                    respuesta = "¡Excelente! 🚀 ¿Cuál es la *carrera* de tu interés? (Responde con el número):\n\n1️⃣ 💼 Administración\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Sistemas\n4️⃣ ⚡ Electricidad\n5️⃣ 🚗 Mec. Automotriz\n6️⃣ ⚙️ Mec. Producción\n7️⃣ 📋 Asistencia Adm."
                else:
                    respuesta = "⚠️ Elige 1, 2 o 3."

            elif estado == "esperando_carrera":
                carreras = {"1": "Administración", "2": "Contabilidad", "3": "Sistemas", "4": "Electricidad", "5": "Mec. Automotriz", "6": "Mec. Producción", "7": "Asistencia Adm."}
                if texto_lower in carreras:
                    user_data[numero]['carrera'] = carreras[texto_lower]
                    user_states[numero] = "menu_principal"
                    guardar_cliente(numero, user_data[numero]['nombres'], user_data[numero]['perfil'], user_data[numero]['carrera'])
                    respuesta = get_menu_principal(user_data[numero]['nombres'].split()[0], is_new=True)
                else:
                    respuesta = "⚠️ Elige un número del 1 al 7."

            # --- MENÚ PRINCIPAL ---
            elif estado == "menu_principal":
                if texto_lower == "0":
                    user_states[numero] = "esperando_nombre"
                    user_data[numero] = {}
                    respuesta = "Entendido. He reiniciado el registro. 👋 ¿Me podrías decir tus *Nombres y Apellidos*? 📝😊"
                elif texto_lower in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]:
                    user_states[numero] = f"submenu_{texto_lower}"
                    respuesta = SUBMENUS[f"submenu_{texto_lower}"]
                elif texto_lower == "11":
                    user_states[numero] = "menu_carreras"
                    respuesta = SUBMENUS["menu_carreras"]
                elif texto_lower == "12":
                    user_states[numero] = "menu_principal" # Se queda aquí
                    respuesta = "🏢 *Atención Personalizada*\n\n📍 Visítanos en: Mz. \"A\", Lote 1, 3er Sector, Grupo 2, Villa El Salvador.\n🕒 Horario: L-V de 8:00 a 13:00 y 14:30 a 21:00.\n\n¡Estamos para ayudarte! 😊\n\n🔹 _Escribe *menú* para volver al índice._"
                else:
                    respuesta = "⚠️ Opción no válida. Elige un número del 1 al 12."

            # --- SUBMENÚS ---
            elif estado in SUBMENUS:
                if texto_lower == "0" or texto_lower == "menú" or texto_lower == "menu":
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(user_data[numero]['nombres'].split()[0])
                elif estado == "menu_carreras" and texto_lower in carreras_info:
                    info = carreras_info[texto_lower]
                    enviar_imagen_meta(numero, info['url'], info['titulo'])
                    enviar_mensaje_meta(numero, info['texto'])
                    respuesta = "📌 ¿Ver otra carrera? Escribe el número o *menú* para volver al índice."
                elif estado in MAPEO_FAQS and texto_lower in MAPEO_FAQS[estado]:
                    faq_id = MAPEO_FAQS[estado][texto_lower]
                    faq_data = get_faq_by_id(faq_id)
                    if faq_data:
                        user_states[numero] = "viendo_info"
                        user_data[numero]['estado_anterior'] = estado # Guarda de dónde vino
                        respuesta = redactar_con_ia(faq_data['preguntas'][0], faq_data)
                        respuesta += "\n\n🔹 _Escribe *0* para volver atrás._\n🔹 _Escribe *menú* para ir al inicio._"
                    else:
                        respuesta = "⚠️ Información no encontrada. Escribe *0* para volver."
                else:
                    respuesta = "⚠️ Opción no válida. Escribe *0* para volver."

            # --- INFORMACIÓN (IA) ---
            elif estado == "viendo_info":
                if texto_lower == "0":
                    estado_anterior = user_data[numero].get('estado_anterior', 'menu_principal')
                    user_states[numero] = estado_anterior
                    respuesta = SUBMENUS[estado_anterior]
                elif texto_lower in ["menú", "menu"]:
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(user_data[numero]['nombres'].split()[0])
                else:
                    respuesta = "🔹 _Escribe *0* para volver atrás o *menú* para ir al inicio._"

            else:
                enviar_fallback_ciclico(numero); enviar_texto_final = False

            # --- ENVÍO FINAL ---
            if enviar_texto_final:
                if not respuesta: 
                    enviar_fallback_ciclico(numero)
                else:
                    enviar_mensaje_meta(numero, respuesta)

    except Exception as e:
        print(f"Error en el flujo: {e}")
    return Response(content="OK", status_code=200)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
