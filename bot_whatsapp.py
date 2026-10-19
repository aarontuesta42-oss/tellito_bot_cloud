import os
import json
import requests
import csv
import random
from datetime import datetime
from difflib import SequenceMatcher
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from dotenv import load_dotenv
from supabase import create_client
import io

# 1. Cargar variables de entorno (PRIMERO se carga el .env)
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
    try:
        # Primero intentamos actualizar si el número ya existe
        response = supabase.table('clientes').update({
            "nombres": nombres, 
            "perfil": perfil, 
            "carrera": carrera
        }).eq('whatsapp', numero).execute()
        
        # Si no se actualizó nada (porque es nuevo), insertamos
        if not response.data:
            supabase.table('clientes').insert({
                "whatsapp": numero, 
                "nombres": nombres, 
                "perfil": perfil, 
                "carrera": carrera
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
    termino_lower = termino.lower()
    for f in faqs:
        if termino_lower in f['subcategoria'].lower():
            if categoria:
                if categoria.lower() in f['categoria'].lower(): return f
            else: return f
        if termino_lower in f['categoria'].lower(): return f
        # ¡NUEVO! Buscar dentro del array de preguntas
        if any(termino_lower in p.lower() for p in f.get('preguntas', [])):
            return f
    return None

def redactar_con_ia(prompt_usuario, contexto_json):
    """Usa DeepSeek para redactar respuestas amigables, inyectando el precio exacto."""
    url = "https://api.deepseek.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    
    precio = None
    # Validar que contexto_json sea un diccionario para evitar el error 'cat'
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
        
        # Si es un diccionario pero no encontramos precio en la iteración, usamos el fallback
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

            # 🚨 ESCUDO ANTI-VISTO (Cancelar en cualquier momento)
            if texto_lower in ["cancelar", "reiniciar", "salir", "menu", "menú"]:
                if estado in ["esperando_nombre", "esperando_perfil", "esperando_carrera"]:
                    user_states[numero] = "esperando_nombre" # <--- ESTA ES LA CLAVE
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
                    elif texto_lower == "4": user_states[numero] = "menu_carreras"; respuesta = "🏫 *CARRERAS*\n\n1️⃣ 💼 Administración de Empresas\n2️⃣ 📊 Contabilidad\n3️⃣ 💻 Desarrollo de Sistemas de Información\n4️⃣ ⚡ Electricidad Industrial\n5️⃣ 🚗 Mecánica Automotriz\n6️⃣ ⚙️ Mecánica de Producción\n7️⃣ 📋 Asistencia Administrativa"
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
                    # Si no encuentra una respuesta exacta, le pasamos TODA la lista de precios y FAQs a la IA para que ella busque
                    if not contexto:
                        contexto = {
                            "info_faqs": faqs,
                            "info_precios": conceptos_pago
                        }
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
            # --- MENÚ DE CARRERAS ---
            elif estado == "menu_carreras":
                carreras_info = {
                    "1": {
                        "url": "https://i.ibb.co/fz7kxPqn/administraci-n.jpg",
                        "titulo": "Administración de Empresas 📈",
                        "texto": """👔 *¡CONVIÉRTETE EN EL PROFESIONAL QUE IMPULSA EL CRECIMIENTO DE LAS EMPRESAS! 🚀*

¿Alguna vez imaginaste dirigir una empresa, liderar un equipo o convertir una gran idea en un negocio exitoso? 💡 Todo gran proyecto necesita personas capaces de tomar decisiones, organizar recursos y descubrir nuevas oportunidades. ¡Tú puedes prepararte para ser una de ellas!

En la carrera de *Administración de Empresas del Instituto J. C. Tello*, desarrollarás las competencias necesarias para comprender el mundo empresarial, gestionar organizaciones y transformar ideas en proyectos con futuro.

✅ *¿QUÉ APRENDERÁS?*
📈 *Marketing estratégico y ventas:* descubre cómo posicionar marcas, atraer clientes y desarrollar estrategias comerciales.
💰 *Finanzas y presupuestos:* aprende a administrar recursos, analizar costos y tomar decisiones económicas responsables.
🤝 *Gestión de recursos humanos:* desarrolla habilidades para liderar equipos, fortalecer el talento y mejorar el ambiente laboral.
📊 *Creación y gestión de negocios:* convierte tus ideas en proyectos organizados, sostenibles y con potencial de crecimiento.

💡 *¿POR QUÉ ESTUDIAR ADMINISTRACIÓN EN EL J. C. TELLO?*
Porque administrar va mucho más allá de organizar documentos o supervisar tareas. Significa comprender cómo funciona una organización, identificar oportunidades y encontrar soluciones que generen resultados. Con una formación orientada al desarrollo de competencias prácticas, podrás fortalecer tu capacidad de liderazgo, análisis y toma de decisiones.

🔥 *UN MUNDO DE OPORTUNIDADES TE ESPERA*
Podrás proyectarte hacia áreas como administración, logística, recursos humanos, marketing, operaciones y gestión comercial. También tendrás la posibilidad de desarrollar tu propio emprendimiento o continuar especializándote para asumir nuevos desafíos.

✨ *Cada empresa necesita personas que sepan hacia dónde avanzar y cómo lograrlo. Prepárate para aportar ideas, tomar decisiones y convertir los desafíos en oportunidades.*

🎓 *Tu futuro profesional comienza con una decisión. ¡Descubre todo lo que puedes lograr estudiando Administración de Empresas en el J. C. Tello!*"""
                    },
                    "2": {
                        "url": "https://i.ibb.co/Jwh71wk4/Contabilidad.jpg",
                        "titulo": "Contabilidad 💰",
                        "texto": """📊 *¡DETRÁS DE CADA GRAN DECISIÓN, HAY UN PROFESIONAL QUE SABE INTERPRETAR LOS NÚMEROS! 💰*

Las empresas necesitan mucho más que ingresos y ventas: necesitan conocer su situación financiera, cumplir sus obligaciones y tomar decisiones basadas en información confiable. Ahí es donde el contador se convierte en una pieza fundamental. 🧾

En la carrera de *Contabilidad del Instituto J. C. Tello*, aprenderás a interpretar la información económica de una organización, controlar sus recursos y contribuir a que sus decisiones tengan una base sólida.

✅ *¿QUÉ APRENDERÁS?*
🧾 *Tributación y auditoría:* comprende las obligaciones tributarias y aprende a revisar la información contable con responsabilidad y criterio profesional.
📉 *Análisis de costos y presupuestos:* identifica costos, organiza presupuestos y aporta información útil para mejorar la gestión de una empresa.
💼 *Gestión financiera y contable:* conoce cómo se registran, analizan y presentan las operaciones económicas de una organización.
💻 *Herramientas y software contable:* desarrolla habilidades para trabajar con herramientas digitales que facilitan los procesos contables y financieros.

💡 *¿QUÉ HACE ESPECIAL A ESTA PROFESIÓN?*
La Contabilidad combina análisis, precisión, responsabilidad y ética. Cada registro cuenta una historia sobre el estado de un negocio, y cada informe puede ayudar a prevenir errores, aprovechar oportunidades y planificar el futuro.

🔥 *CONSTRUYE UNA CARRERA CON MÚLTIPLES POSIBILIDADES*
Podrás orientar tu desarrollo profesional hacia áreas como contabilidad general, tributación, costos, auditoría y gestión financiera. Además, podrás trabajar con empresas de distintos sectores o desarrollar servicios contables de manera independiente.

✨ *La Contabilidad no consiste únicamente en trabajar con números: consiste en generar confianza, proteger los recursos y contribuir al crecimiento de las organizaciones.*

🎓 *Si buscas una profesión donde la precisión, la ética y el conocimiento tengan un impacto real, descubre tu camino en Contabilidad en el Instituto J. C. Tello.*"""
                    },
                    "3": {
                        "url": "https://i.ibb.co/N2WGV9Gy/Desarrollo-de-Sistemas.jpg",
                        "titulo": "Desarrollo de Sistemas 💻",
                        "texto": """💻 *¡NO TE LIMITES A UTILIZAR LA TECNOLOGÍA: APRENDE A CREARLA! 🚀*

Las aplicaciones que utilizamos, las plataformas que conectan al mundo y los sistemas que hacen funcionar a las empresas tienen algo en común: alguien tuvo que diseñarlos y desarrollarlos. ¿Te imaginas ser esa persona? 🌐

En la carrera de *Desarrollo de Sistemas del Instituto J. C. Tello*, podrás convertir tu curiosidad por la tecnología en conocimientos y habilidades para crear soluciones digitales, resolver problemas y participar en un mundo cada vez más conectado.

✅ *¿QUÉ APRENDERÁS?*
🐍 *Programación y lógica computacional:* desarrolla el pensamiento lógico necesario para diseñar algoritmos y construir programas.
🗄️ *Bases de datos y redes:* aprende a organizar información, gestionar datos y comprender cómo se conectan los sistemas informáticos.
🛡️ *Fundamentos de ciberseguridad:* conoce principios y buenas prácticas para proteger la información y utilizar la tecnología de manera responsable.
⚙️ *Desarrollo web y móvil:* descubre cómo se crean sitios web, aplicaciones y herramientas digitales que responden a necesidades reales.

💡 *¿POR QUÉ ELEGIR DESARROLLO DE SISTEMAS?*
Porque la tecnología no solo transforma la forma en que vivimos: también crea nuevas maneras de trabajar, emprender y resolver problemas. Durante tu formación podrás fortalecer tu capacidad de análisis, creatividad y resolución de problemas, construyendo progresivamente las bases para desarrollar tus propios proyectos.

🔥 *TU TALENTO PUEDE LLEGAR MUCHO MÁS LEJOS*
Podrás orientar tu carrera hacia el desarrollo de software, aplicaciones web, bases de datos, soporte de sistemas y otras áreas tecnológicas. Con experiencia, proyectos demostrables y aprendizaje continuo, también podrás explorar oportunidades de trabajo remoto, colaboración internacional o emprendimiento tecnológico.

✨ *Hoy puedes ser quien utiliza una aplicación; mañana, quien diseña una solución que miles de personas necesitan.*

🎓 *El mundo digital necesita personas capaces de pensar, crear y mejorar lo que existe. ¡Empieza a construir tu futuro con Desarrollo de Sistemas en el Instituto J. C. Tello!*"""
                    },
                    "4": {
                        "url": "https://i.ibb.co/yn7ph2S0/electricidad.jpg",
                        "titulo": "Electricidad Industrial ⚡",
                        "texto": """⚡ *¡CONVIÉRTETE EN EL PROFESIONAL QUE MANTIENE LA INDUSTRIA EN MOVIMIENTO! 🔌🏭*

Detrás de una fábrica en funcionamiento, una máquina que trabaja con precisión o un proceso industrial automatizado, existe un sistema eléctrico que necesita instalación, mantenimiento y supervisión especializada.

En la carrera de *Electricidad Industrial del Instituto J. C. Tello*, desarrollarás conocimientos técnicos para comprender, instalar, revisar y mantener sistemas eléctricos utilizados en entornos industriales, con especial atención a la seguridad.

✅ *¿QUÉ APRENDERÁS?*
⚙️ *Instalaciones eléctricas industriales:* comprende los componentes y circuitos que permiten distribuir y utilizar la energía eléctrica.
🤖 *Automatización y control:* conoce los fundamentos de los sistemas de control y los controladores lógicos programables (PLC).
🛠️ *Mantenimiento de máquinas eléctricas:* aprende principios de inspección, diagnóstico y mantenimiento para contribuir al funcionamiento confiable de los equipos.
🦺 *Seguridad eléctrica:* desarrolla hábitos de trabajo seguro, identificación de riesgos y cumplimiento de procedimientos técnicos.

💡 *¿POR QUÉ ELEGIR ESTA CARRERA?*
Porque combina conocimiento técnico, trabajo práctico y resolución de problemas. Aprenderás a comprender sistemas que muchas veces pasan desapercibidos, pero que resultan esenciales para las actividades industriales.

🔥 *ABRE CAMINO A NUEVOS RETOS PROFESIONALES*
Podrás proyectarte hacia actividades de instalación, mantenimiento y asistencia técnica en empresas industriales, plantas de producción, servicios eléctricos y otros sectores que requieran estas competencias.

✨ *Cada sistema que funciona con seguridad demuestra la importancia del conocimiento técnico. Tu trabajo puede contribuir a mantener equipos operativos, procesos eficientes y entornos más seguros.*

🎓 *Si te interesa la tecnología, la energía y el funcionamiento de las máquinas, da el siguiente paso. ¡Estudia Electricidad Industrial en el Instituto J. C. Tello y convierte tu interés en una habilidad profesional!*"""
                    },
                    "5": {
                        "url": "https://i.ibb.co/20z3NB0F/mec-nica-automotriz.jpg",
                        "titulo": "Mecánica Automotriz 🚗",
                        "texto": """🚗 *¡LLEVA TU PASIÓN POR LOS VEHÍCULOS AL SIGUIENTE NIVEL! 🏁🔧*

Los automóviles están evolucionando constantemente. Hoy combinan motores, sensores, sistemas electrónicos y herramientas digitales de diagnóstico. Por eso, conocer un vehículo va mucho más allá de reconocer sus piezas: significa comprender cómo funciona y cómo identificar sus fallas.

Si te interesa descubrir qué ocurre debajo del capó y quieres convertir ese interés en una profesión, la carrera de *Mecánica Automotriz del Instituto J. C. Tello* puede ser el camino para desarrollar tus habilidades técnicas.

✅ *¿QUÉ APRENDERÁS?*
🔧 *Diagnóstico de motores y transmisión:* comprende el funcionamiento de los principales sistemas mecánicos del vehículo.
⚡ *Sistemas eléctricos y electrónicos:* conoce los componentes eléctricos y electrónicos que intervienen en el funcionamiento de los automóviles modernos.
🛞 *Frenos, suspensión y dirección:* estudia los sistemas que contribuyen al control, la estabilidad y la seguridad del vehículo.
🔍 *Diagnóstico con escáner automotriz:* familiarízate con herramientas electrónicas utilizadas para identificar códigos de falla.

💡 *¿POR QUÉ ELEGIR MECÁNICA AUTOMOTRIZ?*
Porque cada diagnóstico es un desafío que exige observación, razonamiento y conocimientos técnicos. La formación práctica y el aprendizaje continuo serán importantes para mantenerte actualizado frente a los cambios tecnológicos del sector automotor.

🔥 *CONSTRUYE TU CAMINO EN EL MUNDO AUTOMOTRIZ*
Podrás orientar tu desarrollo hacia talleres de mantenimiento y reparación, servicios de diagnóstico, empresas del sector automotor o áreas técnicas de concesionarios. También podrás proyectarte hacia el emprendimiento.

✨ *Cada vehículo tiene una historia que contar a través de sus sistemas y componentes. El conocimiento te permite interpretar esas señales y convertir un problema en una solución técnica.*

🎓 *Si disfrutas la mecánica, quieres aprender sobre nuevas tecnologías y buscas una profesión práctica, es momento de dar el siguiente paso. ¡Descubre tu potencial con Mecánica Automotriz en el Instituto J. C. Tello!*"""
                    },
                    "6": {
                        "url": "https://i.ibb.co/39dPy4pq/producci-n.jpg",
                        "titulo": "Mecánica de Producción ⚙️",
                        "texto": """⚙️ *¡CON TUS CONOCIMIENTOS, LAS IDEAS PUEDEN CONVERTIRSE EN REALIDAD! 🏭*

¿Alguna vez te has preguntado cómo se fabrican las piezas de una máquina, cómo se construyen los componentes metálicos o cómo una industria transforma materiales en productos útiles? Detrás de cada proceso de fabricación hay personas que conocen los materiales, dominan las herramientas y saben interpretar los planos.

En la carrera de *Mecánica de Producción del Instituto J. C. Tello*, desarrollarás habilidades técnicas relacionadas con la fabricación, el mecanizado y los procesos utilizados en diferentes actividades industriales.

✅ *¿QUÉ APRENDERÁS?*
🛠️ *Manufactura y soldadura:* conoce procesos de fabricación y unión de materiales utilizados en la elaboración y reparación de componentes.
🖥️ *Tornos, fresadoras y mecanizado CNC:* comprende el funcionamiento de máquinas-herramienta convencionales y de control numérico computarizado.
📐 *Dibujo técnico e interpretación de planos:* aprende a interpretar medidas, símbolos y especificaciones para fabricar piezas conforme a un diseño.
🔄 *Mantenimiento industrial:* conoce fundamentos para revisar equipos, identificar posibles fallas y contribuir a la continuidad de los procesos productivos.

💡 *¿QUÉ HACE ESPECIAL A ESTA CARRERA?*
Combina precisión, razonamiento técnico y trabajo práctico. Cada pieza fabricada exige atención a las medidas, comprensión de los materiales y cumplimiento de procedimientos de calidad y seguridad.

🔥 *TU CONOCIMIENTO PUEDE ABRIRTE NUEVAS OPORTUNIDADES*
Podrás proyectarte hacia empresas manufactureras, talleres de mecanizado, fabricación de estructuras metálicas, mantenimiento industrial y otras actividades productivas.

✨ *La industria necesita personas que sepan transformar un plano en una pieza, una idea en un diseño y un proceso en un resultado de calidad.*

🎓 *Si te gusta construir, fabricar, trabajar con máquinas y comprender cómo se producen las cosas, descubre una carrera donde tus conocimientos pueden convertirse en resultados concretos. ¡Estudia Mecánica de Producción en el Instituto J. C. Tello!*"""
                    },
                    "7": {
                        "url": "https://i.ibb.co/hRCrbRFp/Asistencia-Administrativa.jpg",
                        "titulo": "Asistencia Administrativa 📋",
                        "texto": """📋 *¡DETRÁS DE UNA ORGANIZACIÓN EFICIENTE, HAY PERSONAS QUE HACEN QUE TODO FUNCIONE! ✨*

En una empresa, cada reunión, documento, trámite y comunicación necesita organización. Cuando la información está bien gestionada y los procesos se desarrollan de manera ordenada, todo el equipo puede trabajar mejor.

En la carrera de *Asistencia Administrativa del Instituto J. C. Tello*, desarrollarás las habilidades necesarias para convertirte en un profesional organizado, resolutivo y preparado para afrontar las exigencias del entorno laboral.

✅ *¿QUÉ APRENDERÁS?*
💻 *Ofimática y herramientas digitales:* utiliza aplicaciones informáticas para elaborar documentos, organizar datos y facilitar las tareas administrativas.
🗂️ *Archivo y gestión documental:* aprende a clasificar, organizar, conservar y localizar información de manera eficiente.
🗣️ *Atención al cliente y comunicación:* desarrolla habilidades interpersonales para atender consultas y comunicarte de manera profesional.
📝 *Redacción comercial y empresarial:* aprende a elaborar comunicaciones, informes, cartas y documentos utilizados en las organizaciones.

💡 *¿POR QUÉ ELEGIR ASISTENCIA ADMINISTRATIVA?*
Porque una buena organización puede marcar la diferencia entre un proceso complicado y uno eficiente. Esta carrera te permitirá fortalecer la responsabilidad, la comunicación, la planificación y el manejo de herramientas digitales.

🔥 *PREPÁRATE PARA FORMAR PARTE DE DISTINTOS SECTORES LABORALES*
Podrás orientar tu desarrollo profesional hacia funciones de asistencia administrativa, recepción, gestión documental, apoyo de oficina y coordinación administrativa en empresas privadas, instituciones públicas y otras organizaciones.

✨ *Ser un buen profesional administrativo significa anticiparse a las necesidades, cuidar los detalles y facilitar el trabajo de todo un equipo. Tu aporte puede ser decisivo para que una organización alcance sus objetivos.*

🎓 *Si eres una persona organizada, te gusta coordinar actividades y quieres desarrollar habilidades útiles para el mundo laboral, encuentra tu oportunidad en Asistencia Administrativa del Instituto J. C. Tello.*"""
                    }
                }
                
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
                    
            else:
                res_json = buscar_faq(texto)
                if res_json: respuesta = redactar_con_ia(texto, res_json)
                else: enviar_fallback_ciclico(numero); enviar_texto_final = False

            # --- ENVÍO FINAL ---
            if enviar_texto_final:
                if not respuesta: 
                    enviar_fallback_ciclico(numero)
                else:
                    # Obtenemos el estado ACTUAL (después de procesar el mensaje)
                    estado_actual = user_states.get(numero, "menu_principal")

                    # Agregamos el pie de página manualmente. Sin .replace() que rompan palabras.
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
