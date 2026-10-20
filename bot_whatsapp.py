import os
import json
import random
import requests
import time
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
carreras_info = data.get('info_carreras', {})
fallbacks = data.get('fallbacks', [])

user_states = {}
user_data = {}

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
    # 1. Mensaje de felicitación
    enviar_mensaje_meta(numero, "🎉 ¡Felicidades! Acabas de descubrir un *Easter Egg* de Tellito Bot. 🕵️‍♂️\n\nNo olvides compartir este secreto con tus amigos. 😉")
    
    # Pausa para que no se envíen los mensajes pegados
    import time
    time.sleep(1.5)
    
    # 2. Imagen de Fallout con el texto en primera persona
    url_imagen = "https://i.ibb.co/7tJZHVc2/1.png" # La primera imagen de Fallout
    texto_imagen = "🛡️ *Yo no salgo a la superficie del instituto, yo realizo mis consultas con Tellito Bot.*\n\n¡Comparte esta imagen con tus amigos para que también descubran la magia de la IA! 🤖✨"
    
    enviar_imagen_meta(numero, url_imagen, texto_imagen)
    time.sleep(1.5)
    
    # 3. Enviar el menú principal de nuevo para evitar el bucle
    if numero in user_data and 'nombres' in user_data[numero]:
        nombres = user_data[numero]['nombres']
        primer_nombre = nombres.split()[0] if nombres else "amigo"
        menu = get_menu_principal(primer_nombre, is_new=False)
        enviar_mensaje_meta(numero, menu)
    else:
        enviar_mensaje_meta(numero, "Para comenzar, por favor dime tu *Nombre y Apellido*.")

# --- BÚSQUEDA DE FAQ ---
def get_faq_by_id(faq_id):
    for f in faqs:
        if f.get('id') == faq_id:
            return f
    return None

# --- IA (SOLO REDACTA) ---
def redactar_con_ia(faq_dict):
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    
    contexto_str = json.dumps(faq_dict, indent=2, ensure_ascii=False)

    # 🚨 LÓGICA INTELIGENTE: Solo ponemos la regla de pago si el trámite tiene costo
    regla_pago = ""
    if faq_dict.get('costo'):
        regla_pago = (
            f"REGLA DE ORO: Como este trámite tiene un costo, SIEMPRE debes recordar al usuario:\n"
            f"1. Pagar en el Banco de la Nación (Cta. Cte. N° 00000-289078).\n"
            f"2. Llevar el voucher ORIGINAL y una COPIA a Caja.\n"
        )

    prompt_sistema = (
        f"Eres Tellito Bot, el asistente virtual del IESTP Julio César Tello. "
        f"Tu única tarea es redactar la información del trámite de forma amigable, clara y CONCISA. Usa emojis. "
        f"Aquí tienes la información oficial:\n\n{contexto_str}\n\n"
        f"{regla_pago}"
        f"REGLAS DE FORMATO ESTRICTAS (ESTÁS EN WHATSAPP):\n"
        f"- Usa SOLO un asterisco a cada lado para negrita: *texto*\n"
        f"- Usa guion bajo a cada lado para cursiva: _texto_\n"
        f"- NO uses almohadillas (#) ni doble asterisco (**).\n"
        f"- ¡PROHIBIDO! NO agregues menús de opciones (1, 2, 3...). NO hagas preguntas de seguimiento. Solo entrega la información."
    )
    
    payload = {
        "model": "deepseek-chat",
        "messages": [{"role": "system", "content": prompt_sistema}, {"role": "user", "content": "Redacta la información oficial."}],
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
    "submenu_2": "💰 *COSTOS DE POSTULAR*\n\n1️⃣ Costo de Inscripción y Prospecto\n2️⃣ Costo de Matrícula\n3️⃣ ¿Cómo pagar y canjear el voucher?\n\n0️⃣ Volver al menú principal",
    "submenu_5": "📄 *TRÁMITES ACADÉMICOS*\n\n1️⃣ Cambio de Turno\n2️⃣ Subsanación de Cursos\n3️⃣ Convalidación de Cursos\n\n0️⃣ Volver al menú principal",
    "submenu_6": "📑 *CERTIFICADOS Y CONSTANCIAS*\n\n1️⃣ Certificado de Estudios\n2️⃣ Constancia de Egresado\n3️⃣ Duplicado de Título\n\n0️⃣ Volver al menú principal",
    "submenu_7": "🎓 *MATRÍCULA*\n\n1️⃣ Ratificación de Matrícula\n2️⃣ Reserva de Matrícula\n3️⃣ Licencia de Estudios\n\n0️⃣ Volver al menú principal",
    "submenu_8": "🎉 *TITULACIÓN*\n\n1️⃣ Trabajo de Aplicación Profesional (TAP)\n2️⃣ Examen de Suficiencia Profesional\n3️⃣ Requisitos para Titularse\n\n0️⃣ Volver al menú principal",
    "menu_carreras": "🏫 *NUESTRAS CARRERAS*\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa\n\n0️⃣ Volver al menú principal",
}

MAPEO_FAQS = {
    "submenu_1": {"1": "FAQ-001", "2": "FAQ-002", "3": "FAQ-003"},
    "submenu_2": {"1": "FAQ-004", "2": "FAQ-005", "3": "FAQ-023"},
    "submenu_5": {"1": "FAQ-008", "2": "FAQ-009", "3": "FAQ-010"},
    "submenu_6": {"1": "FAQ-011", "2": "FAQ-012", "3": "FAQ-013"},
    "submenu_7": {"1": "FAQ-014", "2": "FAQ-015", "3": "FAQ-016"},
    "submenu_8": {"1": "FAQ-017", "2": "FAQ-018", "3": "FAQ-019"},
}

# FAQs directas (sin submenú)
DIRECTOS = {
    "3": "FAQ-006",
    "4": "FAQ-007",
    "9": "FAQ-020",
    "10": "FAQ-021",
    "12": "FAQ-022"
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
                # Formateamos el nombre para que empiece con mayúscula (Ej: aaron -> Aaron)
                nombre_formateado = texto.title()
                user_data[numero]['nombres'] = nombre_formateado
                user_states[numero] = "esperando_perfil"
                
                # Extraemos solo el primer nombre para el saludo
                primer_nombre = nombre_formateado.split()[0] if nombre_formateado.split() else "amigo"
                
                respuesta = f"¡Un gusto, *{primer_nombre}*! 🤝\n\n¿Cuál es tu situación actual?\n\n1️⃣ 🎓 Soy *Prospecto* (me gustaría postular al instituto)\n2️⃣ 📚 Soy *Estudiante* (estoy actualmente estudiando en el instituto)\n3️⃣ 🎉 Soy *Egresado* (ya terminé mi carrera del instituto)\n\n_Responde únicamente con el número de tu opción._"

            elif estado == "esperando_perfil":
                perfiles = {"1": "Prospecto", "2": "Estudiante", "3": "Egresado"}
                if texto_lower in perfiles:
                    user_data[numero]['perfil'] = perfiles[texto_lower]
                    user_states[numero] = "esperando_carrera"
                    respuesta = "¡Excelente! 🚀 ¿Cuál es la *carrera* de tu interés? (Responde con el número):\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa\n\n_Responde únicamente con el número de la carrera._"
                else:
                    # Error amigable sin easter egg
                    respuesta = "⚠️ Por favor, elige una opción válida enviando solo el número: 1, 2 o 3."

            elif estado == "esperando_carrera":
                carreras = {"1": "Administración", "2": "Contabilidad", "3": "Sistemas", "4": "Electricidad", "5": "Mec. Automotriz", "6": "Mec. Producción", "7": "Asistencia Adm."}
                if texto_lower in carreras:
                    user_data[numero]['carrera'] = carreras[texto_lower]
                    user_states[numero] = "menu_principal"
                    guardar_cliente(numero, user_data[numero]['nombres'], user_data[numero]['perfil'], user_data[numero]['carrera'])
                    respuesta = get_menu_principal(user_data[numero]['nombres'].split()[0], is_new=True)
                else:
                    # Error amigable sin easter egg
                    respuesta = "⚠️ Por favor, elige una opción válida enviando solo el número del 1 al 7."

            # --- MENÚ PRINCIPAL ---
            elif estado == "menu_principal":
                if texto_lower == "0":
                    user_states[numero] = "esperando_nombre"
                    user_data[numero] = {}
                    respuesta = "Entendido. He reiniciado el registro. 👋 ¿Me podrías decir tus *Nombres y Apellidos*? 📝😊"
                
                # Submenús
                elif texto_lower in ["1", "2", "5", "6", "7", "8"]:
                    user_states[numero] = f"submenu_{texto_lower}"
                    respuesta = SUBMENUS[f"submenu_{texto_lower}"]
                
                # Menú de carreras
                elif texto_lower == "11":
                    user_states[numero] = "menu_carreras"
                    respuesta = SUBMENUS["menu_carreras"]
                
                # Opciones Directas (3, 4, 9, 10, 12)
                elif texto_lower in DIRECTOS:
                    faq_id = DIRECTOS[texto_lower]
                    faq_data = get_faq_by_id(faq_id)
                    if faq_data:
                        user_states[numero] = "viendo_info"
                        user_data[numero]['estado_anterior'] = "menu_principal"
                        respuesta = redactar_con_ia(faq_data)
                        respuesta += "\n\n🔹 _Escribe *menú* para ir al inicio._"
                    else:
                        respuesta = "⚠️ Información no encontrada."
                
                # Easter egg / Fallback
                else:
                    enviar_fallback_ciclico(numero)
                    enviar_texto_final = False

            # --- SUBMENÚS ---
            elif estado in SUBMENUS:
                if texto_lower == "0" or texto_lower in ["menú", "menu"]:
                    user_states[numero] = "menu_principal"
                    respuesta = get_menu_principal(user_data[numero]['nombres'].split()[0])
                
                elif estado == "menu_carreras" and texto_lower in carreras_info:
                    info = carreras_info[texto_lower]
                    # 1. Enviamos la imagen
                    enviar_imagen_meta(numero, info['url'], info['titulo'])
                    
                    # 2. Hacemos una pausa de 1 segundo para que la imagen se cargue primero
                    import time
                    time.sleep(1.5)
                    
                    # 3. Enviamos el texto
                    enviar_mensaje_meta(numero, info['texto'])
                    respuesta = "📌 ¿Ver otra carrera? Escribe el número o *menú* para volver al índice."
                
                elif estado in MAPEO_FAQS and texto_lower in MAPEO_FAQS[estado]:
                    faq_id = MAPEO_FAQS[estado][texto_lower]
                    faq_data = get_faq_by_id(faq_id)
                    if faq_data:
                        user_states[numero] = "viendo_info"
                        user_data[numero]['estado_anterior'] = estado
                        respuesta = redactar_con_ia(faq_data)
                        respuesta += "\n\n🔹 _Escribe *0* para volver atrás._\n🔹 _Escribe *menú* para ir al inicio._"
                    else:
                        respuesta = "⚠️ Información no encontrada. Escribe *0* para volver."
                else:
                    enviar_fallback_ciclico(numero)
                    enviar_texto_final = False

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
                    enviar_fallback_ciclico(numero)
                    enviar_texto_final = False

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
