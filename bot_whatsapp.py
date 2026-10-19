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
carreras_info = data.get('info_carreras', {}) # Carga las carreras desde el JSON

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
def get_faq_by_id(faq_id):
    for f in faqs:
        if f.get('id') == faq_id:
            return f
    return None

# --- IA DEEPSEEK (Solo redacta, no busca) ---
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
        f"- ¡PROHIBIDO! NO agregues menús de opciones (1, 2, 3...). NO agregues textos como 'Escribe menú' o 'Escribe 0'. "
        f"- ¡PROHIBIDO! NO hagas preguntas de seguimiento al final como '¿Cuál te gustaría conocer mejor?'. Solo entrega la información y termina el mensaje."
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

# --- MENÚ MAESTRO ---
def get_menu_principal(primer_nombre, is_new=False):
    if is_new: 
        menu = f"¡Todo listo, *{primer_nombre}*! 🚀 Ya estás registrado en el sistema. Aquí tienes nuestro índice de consultas:\n\n"
    else: 
        menu = f"¡Hola de nuevo, *{primer_nombre}*! 👋 ¿En qué te puedo ayudar hoy? Aquí tienes el índice:\n\n"
    
    menu += "🎓 *INFORMACIÓN DE PROSPECTO*\n"
    menu += "1️⃣ Modalidades de Admisión\n2️⃣ Costos de Postular\n3️⃣ Requisitos de Matrícula\n4️⃣ Traslado Externo\n\n"
    
    menu += "📚 *INFORMACIÓN DE ESTUDIANTE*\n"
    menu += "5️⃣ Trámites Académicos (Turno, Subsanación)\n6️⃣ Certificados y Constancias\n7️⃣ Ratificación, Reserva o Licencia\n\n"
    
    menu += "🎉 *INFORMACIÓN DE EGRESADO*\n"
    menu += "8️⃣ Titulación (TAP o Examen)\n9️⃣ Constancia de Egresado\n🔟 Certificación Modular\n\n"
    
    menu += "🏫 *GENERAL*\n"
    menu += "1️⃣1️⃣ Nuestras Carreras\n1️⃣2️⃣ Contacto y Ubicación\n\n"
    
    menu += "0️⃣ Reiniciar registro"
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

            # 🚨 CANCELAR O REINICIAR
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
                    respuesta = get_menu_principal(cliente_existente['nombres'].split()[0], is_new=False)
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
                    enviar_mensaje_meta(numero, "⚠️ Elige 1, 2 o 3 (o escribe 'cancelar' para reiniciar).")
                    return Response(content="OK", status_code=200)
                
                user_data[numero]['perfil'] = perfil
                user_states[numero] = "esperando_carrera"
                pregunta = "¡Excelente! 🚀 Ahora dime, ¿cuál es la *carrera de tu interés*? (Responde con el número):" if perfil == "Prospecto" else "¡Excelente! 🎓 Ahora dime, ¿*a qué carrera perteneces*? (Responde con el número):"
                respuesta = f"{pregunta}\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas de Información\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa"

            elif estado == "esperando_carrera":
                carreras = {"1": "Administración de Empresas", "2": "Contabilidad", "3": "Desarrollo de Sistemas", "4": "Electricidad Industrial", "5": "Mecánica Automotriz", "6": "Mecánica de Producción", "7": "Asistencia Administrativa"}
                if texto_lower in carreras: carrera_elegida = carreras[texto_lower]
                else: 
                    enviar_mensaje_meta(numero, "⚠️ Elige un número del 1 al 7 (o escribe 'cancelar' para reiniciar).")
                    return Response(content="OK", status_code=200)
                
                user_data[numero]['carrera'] = carrera_elegida
                user_states[numero] = "menu_principal"
                guardar_cliente(numero, user_data[numero]['nombres'], user_data[numero]['perfil'], user_data[numero]['carrera'])
                respuesta = get_menu_principal(user_data[numero]['nombres'].split()[0], is_new=True)

            # --- MENÚ PRINCIPAL (ÍNDICE ÚNICO) ---
            elif estado == "menu_principal":
                if texto_lower == "0":
                    user_states[numero] = "esperando_nombre"
                    user_data[numero] = {}
                    respuesta = "Entendido. He reiniciado el registro. 👋 ¿Me podrías decir tus *Nombres y Apellidos*? 📝😊"
                
                # Prospecto
                elif texto_lower == "1": respuesta = redactar_con_ia("Explícame las modalidades de admisión", get_faq_by_id("FAQ-014"))
                elif texto_lower == "2": respuesta = redactar_con_ia("Explícame los costos de postular", get_faq_by_id("FAQ-017"))
                elif texto_lower == "3": respuesta = redactar_con_ia("Explícame los requisitos de matrícula", get_faq_by_id("FAQ-029"))
                elif texto_lower == "4": respuesta = redactar_con_ia("Explícame el traslado externo", get_faq_by_id("FAQ-074"))
                
                # Estudiante
                elif texto_lower == "5": respuesta = redactar_con_ia("Explícame los trámites académicos (cambio de turno, subsanación, convalidación)", get_faq_by_id("FAQ-117"))
                elif texto_lower == "6": respuesta = redactar_con_ia("Explícame los certificados y constancias", get_faq_by_id("FAQ-080"))
                elif texto_lower == "7": respuesta = redactar_con_ia("Explícame ratificación, reserva o licencia", get_faq_by_id("FAQ-037"))
                
                # Egresado
                elif texto_lower == "8": respuesta = redactar_con_ia("Explícame las modalidades de titulación", get_faq_by_id("FAQ-092"))
                elif texto_lower == "9": respuesta = redactar_con_ia("Explícame la constancia de egresado", get_faq_by_id("FAQ-086"))
                elif texto_lower == "10": respuesta = redactar_con_ia("Explícame la certificación modular", get_faq_by_id("FAQ-110"))
                
                # General
                elif texto_lower == "11": user_states[numero] = "menu_carreras"; respuesta = "🏫 *CARRERAS*\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas de Información\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa"
                elif texto_lower == "12": respuesta = "🏢 *Atención Personalizada*\n\n📍 Visítanos en: Mz. \"A\", Lote 1, 3er Sector, Grupo 2, Villa El Salvador.\n🕒 Horario: L-V de 8:00 a 13:00 y 14:30 a 21:00.\n\n¡Estamos para ayudarte! 😊"
                else:
                    respuesta = "⚠️ Opción no válida. Por favor, elige un número del menú principal."

            # --- MENÚ DE CARRERAS ---
            elif estado == "menu_carreras":
                if texto_lower in carreras_info:
                    info = carreras_info[texto_lower]
                    enviar_imagen_meta(numero, info['url'], info['titulo'])
                    enviar_mensaje_meta(numero, info['texto'])
                    respuesta = "📌 ¿Ver otra carrera? Escribe el número o *menú* para volver al índice principal."
                else:
                    enviar_fallback_ciclico(numero); enviar_texto_final = False

            else:
                enviar_fallback_ciclico(numero); enviar_texto_final = False

            # --- ENVÍO FINAL ---
            if enviar_texto_final:
                if not respuesta: 
                    enviar_fallback_ciclico(numero)
                else:
                    estado_actual = user_states.get(numero, "menu_principal")
                    
                    if estado_actual == "menu_principal":
                        respuesta += "\n\n🔹 _Escribe el número de tu opción._"
                    elif estado_actual == "menu_carreras":
                        respuesta += "\n\n🔹 _Escribe *menú* para volver al índice principal._"
                    
                    enviar_mensaje_meta(numero, respuesta)

    except Exception as e:
        print(f"Error en el flujo: {e}")
    return Response(content="OK", status_code=200)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
