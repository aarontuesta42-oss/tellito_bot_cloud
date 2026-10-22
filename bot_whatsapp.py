# ==========================================================
# 🤖 TELLITO BOT — IESTP Julio César Tello
# ----------------------------------------------------------
# Este archivo contiene SOLO lógica. Ningún texto, menú, dato ni
# Easter egg vive aquí: todo está en base_conocimiento.json
# (y en config_bot.json, bajo la clave "bot", si no la has fusionado).
# ==========================================================
import os
import re
import json
import time
import hmac
import hashlib
import threading
import traceback
import unicodedata
from collections import defaultdict, deque
from datetime import datetime, timezone
from itertools import combinations_with_replacement

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import PlainTextResponse
from supabase import create_client

from prompts import PROMPT_FAQ, PROMPT_LIBRE

# ----------------------------------------------------------
# 1. ENTORNO
# ----------------------------------------------------------
load_dotenv()

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN")
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID")
META_APP_SECRET = os.getenv("META_APP_SECRET")  # opcional: valida la firma de Meta
META_GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v21.0")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

DEEPSEEK_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"
META_URL = f"https://graph.facebook.com/{META_GRAPH_VERSION}/{META_PHONE_NUMBER_ID}/messages"
TABLA_SESIONES = "sesiones_bot"

http = requests.Session()
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()


def log(*args):
    print(*args, flush=True)


if not META_APP_SECRET:
    log("⚠️ META_APP_SECRET no está definido: el webhook NO verifica la firma de Meta.")

# ----------------------------------------------------------
# 2. CONTENIDO (JSON)
# ----------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _cargar_json(nombre):
    with open(os.path.join(BASE_DIR, nombre), "r", encoding="utf-8") as f:
        return json.load(f)


data = _cargar_json("base_conocimiento.json")
if "bot" not in data:
    data.update(_cargar_json("config_bot.json"))

BOT = data["bot"]
AJ = BOT["ajustes"]
MSG = BOT["mensajes"]
LENG = BOT["lenguaje"]
MENUS = BOT["menus"]
SUBMENUS = BOT["submenus"]
MAPEO_FAQS = BOT["mapeo_faqs"]
DIRECTOS = BOT["directos"]
REGISTRO = BOT["registro"]
SALUDOS_MENU = BOT["saludos_menu"]
PIES = BOT["pies"]
ETIQ = BOT["etiquetas"]
PAGOS = BOT["pagos"]
EASTER = BOT["easter_egg"]
IA_CFG = BOT["ia"]

faqs = data["faqs"]
carreras_info = data.get("info_carreras", {})
metadata = data.get("metadata", {})
INSTITUCION = metadata.get("institucion", "")

PAUSA = float(AJ["pausa_segundos"])
LONG_LIBRE = int(AJ["longitud_texto_libre"])


def render(plantilla, **kw):
    for k, v in kw.items():
        plantilla = plantilla.replace("{" + k + "}", str(v))
    return plantilla


def _verificar_config():
    """Falla al arrancar (con un mensaje claro) si falta algo del JSON."""
    faltan = []
    for clave in ("ajustes", "lenguaje", "atajos", "registro", "saludos_menu", "mensajes", "pies",
                  "etiquetas", "pagos", "menus", "submenus", "mapeo_faqs", "directos",
                  "easter_egg", "sensibles", "validador", "ia"):
        if clave not in BOT:
            faltan.append(clave)
    if faltan:
        raise RuntimeError(f"Falta en el JSON la clave bot.{', bot.'.join(faltan)}")
    ids = {f.get("id") for f in faqs}
    ids_usados = set(DIRECTOS.values()) | {v for m in MAPEO_FAQS.values() for v in m.values()}
    for faq_id in sorted(ids_usados - ids):
        log(f"⚠️ El menú apunta a {faq_id}, que no existe en faqs.")
    for clave in MAPEO_FAQS:
        if clave not in SUBMENUS:
            log(f"⚠️ mapeo_faqs.{clave} no tiene submenú en submenus.")


_verificar_config()

# ----------------------------------------------------------
# 3. TEXTO: NORMALIZACIÓN Y DETECCIÓN
# ----------------------------------------------------------


def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-z0-9\s]", " ", t.lower())
    return re.sub(r"\s+", " ", t).strip()


SALUDOS = {normalizar(s) for s in LENG["saludos"]}
AGRADECIMIENTOS = {normalizar(s) for s in LENG["agradecimientos"]}
INTERROGATIVOS = [normalizar(s) for s in LENG["interrogativos"]]
NOMBRES_NO_VALIDOS = {normalizar(s) for s in LENG["nombres_no_validos"]}
COMANDOS_MENU = {normalizar(s) for s in LENG["comandos_menu"]}
COMANDOS_REINICIO = {normalizar(s) for s in LENG["comandos_reinicio_registro"]}
ATAJOS = {normalizar(k): v for k, v in BOT["atajos"].items()}
SENSIBLES = [normalizar(s) for s in BOT["sensibles"]["palabras"]]
_PREF_OPCION = "|".join(re.escape(normalizar(p)) for p in sorted(LENG["prefijos_opcion"], key=len, reverse=True))
_PREF_NOMBRE = "|".join(re.escape(p) for p in sorted(LENG["prefijos_nombre"], key=len, reverse=True))
RE_PREFIJO_OPCION = re.compile(rf"(?:{_PREF_OPCION})\s+(\d+)")
RE_PREFIJO_NOMBRE = re.compile(rf"^(?:{_PREF_NOMBRE})\s+", re.IGNORECASE)
RE_NOMBRE = re.compile(r"[A-Za-zÁÉÍÓÚÜÑáéíóúüñ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’.\- ]*")


def limpiar_comando(texto: str) -> str:
    """'09' -> '9' | 'carreras 11' -> '11' | 'Menú!' -> 'menu' | '1️⃣1️⃣' -> '11'."""
    t = texto
    for k, v in LENG["reemplazos"].items():
        t = t.replace(k, f" {v} ")
    t = normalizar(t)
    m = RE_PREFIJO_OPCION.fullmatch(t)
    if m:
        t = m.group(1)
    if t.isdigit():
        t = str(int(t))
    return t


def es_saludo(cmd: str) -> bool:
    c = re.sub(r"(.)\1{2,}", r"\1", cmd)
    if not c or len(c.split()) > 4:
        return False
    return any(c == s or c.startswith(s + " ") for s in SALUDOS)


def es_agradecimiento(cmd: str) -> bool:
    return re.sub(r"(.)\1{2,}", r"\1", cmd) in AGRADECIMIENTOS


def parece_pregunta(texto: str) -> bool:
    tokens = normalizar(texto).split()
    if len(tokens) < 2:
        return False
    if "?" in texto or "¿" in texto:
        return True
    frase = " ".join(tokens)
    return any(frase.startswith(i + " ") for i in INTERROGATIVOS)


def es_consulta(texto: str, cmd: str) -> bool:
    """Texto libre que merece respuesta de la IA (no es opción, saludo ni atajo)."""
    if not cmd or cmd.isdigit() or cmd in ATAJOS or es_saludo(cmd) or es_agradecimiento(cmd):
        return False
    return len(texto) > LONG_LIBRE or parece_pregunta(texto)


def es_sensible(texto: str) -> bool:
    n = " " + normalizar(texto) + " "
    return any(s in n for s in SENSIBLES)


def validar_nombre(texto: str):
    """Devuelve el nombre formateado o None si no parece un nombre real."""
    t = RE_PREFIJO_NOMBRE.sub("", texto.strip()).strip()
    if not t or len(t) > int(AJ["nombre_max_caracteres"]) or not RE_NOMBRE.fullmatch(t):
        return None
    palabras = t.split()
    if len(palabras) > int(AJ["nombre_max_palabras"]):
        return None
    letras = re.sub(r"[^A-Za-zÁÉÍÓÚÜÑáéíóúüñ]", "", t)
    if len(letras) < int(AJ["nombre_min_letras"]):
        return None
    n = normalizar(t)
    if n in NOMBRES_NO_VALIDOS or all(p in NOMBRES_NO_VALIDOS for p in n.split()):
        return None
    if es_saludo(n) or not re.search(r"[aeiou]", n):
        return None
    return t.title()


def nombre_parece_consulta(texto: str) -> bool:
    """En el paso del nombre: ¿la persona hizo una pregunta en vez de dar su nombre?"""
    tokens = texto.split()
    if len(tokens) < 2:
        return False
    if parece_pregunta(texto) or any(c.isdigit() for c in texto):
        return True
    return len(tokens) > int(AJ["nombre_max_palabras"])


# ----------------------------------------------------------
# 4. ENVÍO A WHATSAPP (Meta)
# ----------------------------------------------------------


def _post_meta(payload) -> bool:
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    for _ in range(2):
        try:
            r = http.post(META_URL, headers=headers, json=payload, timeout=15)
            if r.status_code < 300:
                return True
            log(f"❌ Meta {r.status_code}: {r.text[:300]}")
            if r.status_code < 500:
                return False
        except requests.RequestException as e:
            log(f"❌ Meta (red): {e}")
    return False


def _partir(texto: str, limite: int):
    if len(texto) <= limite:
        return [texto]
    partes, actual = [], ""
    for parrafo in texto.split("\n\n"):
        while len(parrafo) > limite:
            corte = parrafo.rfind("\n", 0, limite)
            corte = corte if corte > 0 else limite
            if actual:
                partes.append(actual)
                actual = ""
            partes.append(parrafo[:corte])
            parrafo = parrafo[corte:].lstrip("\n")
        if len(actual) + len(parrafo) + 2 > limite and actual:
            partes.append(actual)
            actual = parrafo
        else:
            actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    if actual:
        partes.append(actual)
    return partes


def enviar_texto(numero, texto):
    if not texto:
        return
    for i, parte in enumerate(_partir(texto, int(AJ["max_chars_mensaje"]))):
        if i:
            time.sleep(0.6)
        _post_meta({"messaging_product": "whatsapp", "to": numero, "type": "text",
                    "text": {"body": parte, "preview_url": False}})


def enviar_imagen(numero, url_imagen, caption=""):
    """Si el caption es muy largo o la imagen falla, el texto igual llega."""
    limite = int(AJ["max_chars_caption"])
    imagen = {"link": url_imagen}
    if caption and len(caption) <= limite:
        imagen["caption"] = caption
        caption_enviado = True
    else:
        caption_enviado = not caption
    ok = _post_meta({"messaging_product": "whatsapp", "to": numero, "type": "image", "image": imagen})
    if caption and not (ok and caption_enviado):
        enviar_texto(numero, caption)
    return ok

def enviar_botones(numero, texto, opciones):
    """Envía hasta 3 botones. opciones = [("1", "Opción 1"), ("2", "Opción 2")]"""
    botones = [{"type": "reply", "reply": {"id": op_id, "title": op_titulo[:20]}} for op_id, op_titulo in opciones]
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto},
            "action": {"buttons": botones}
        }
    }
    _post_meta(payload)

def enviar_lista(numero, texto, titulo_boton, secciones):
    """Envía una lista (hasta 10 items). secciones = [{"title": "Sección 1", "rows": [{"id": "1", "title": "Opción 1"}]}]"""
    payload = {
        "messaging_product": "whatsapp",
        "to": numero,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "header": {"type": "text", "text": "Tellito Bot 🤖"},
            "body": {"text": texto},
            "action": {
                "button": titulo_boton[:20],
                "sections": secciones
            }
        }
    }
    _post_meta(payload)

def pausa():
    time.sleep(PAUSA)


# ----------------------------------------------------------
# 5. BASE DE DATOS (Supabase)
# ----------------------------------------------------------
_persistencia_apagada_hasta = 0.0


def _apagar_persistencia(e):
    global _persistencia_apagada_hasta
    if time.time() >= _persistencia_apagada_hasta:
        log(f"⚠️ Sesiones solo en memoria por 5 min (¿existe la tabla {TABLA_SESIONES}?): {e}")
    _persistencia_apagada_hasta = time.time() + 300


def guardar_cliente(numero, nombres, perfil, carrera):
    try:
        r = supabase.table("clientes").update(
            {"nombres": nombres, "perfil": perfil, "carrera": carrera}
        ).eq("whatsapp", numero).execute()
        if not r.data:
            supabase.table("clientes").insert(
                {"whatsapp": numero, "nombres": nombres, "perfil": perfil, "carrera": carrera}
            ).execute()
        log(f"✅ Cliente guardado: {nombres}")
    except Exception as e:
        log(f"❌ Error BD (clientes): {e}")


def cargar_cliente(numero):
    try:
        r = supabase.table("clientes").select("*").eq("whatsapp", numero).execute()
        if r.data and r.data[0].get("nombres"):
            return {"nombres": r.data[0]["nombres"], "perfil": r.data[0].get("perfil"),
                    "carrera": r.data[0].get("carrera")}
    except Exception as e:
        log(f"❌ Error BD (clientes): {e}")
    return None


def _cargar_sesion_db(numero):
    if time.time() < _persistencia_apagada_hasta:
        return None
    try:
        r = supabase.table(TABLA_SESIONES).select("estado,datos").eq("whatsapp", numero).limit(1).execute()
        if r.data:
            datos = r.data[0].get("datos") or {}
            if isinstance(datos, str):
                datos = json.loads(datos)
            return {"estado": r.data[0].get("estado") or "inicio", "datos": datos}
    except Exception as e:
        _apagar_persistencia(e)
    return None


def _guardar_sesion_db(numero, ses):
    if time.time() < _persistencia_apagada_hasta:
        return
    try:
        supabase.table(TABLA_SESIONES).upsert(
            {"whatsapp": numero, "estado": ses["estado"], "datos": ses["datos"],
             "actualizado": datetime.now(timezone.utc).isoformat()},
            on_conflict="whatsapp",
        ).execute()
    except Exception as e:
        _apagar_persistencia(e)


sesiones = {}


def obtener_sesion(numero):
    ses = sesiones.get(numero)
    if ses is None:
        ses = _cargar_sesion_db(numero) or {"estado": "inicio", "datos": {}}
        sesiones[numero] = ses
    return ses


def _huella(ses):
    return json.dumps([ses["estado"], ses["datos"]], sort_keys=True, ensure_ascii=False)


def primer_nombre(ses):
    nombres = (ses["datos"].get("nombres") or "").split()
    return nombres[0] if nombres else MSG["nombre_defecto"]


# ----------------------------------------------------------
# 6. CONOCIMIENTO PARA LA IA + VALIDADOR
# ----------------------------------------------------------


def _nombre_carrera(titulo):
    return re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ\s]", "", titulo).strip()


LISTA_CARRERAS = ", ".join(_nombre_carrera(v["titulo"]) for v in carreras_info.values())
TOTAL_CARRERAS = len(carreras_info)
PALABRAS_DETALLE = [normalizar(p) for p in IA_CFG["palabras_detalle_carreras"]]


def construir_contexto(texto_usuario: str = "") -> str:
    partes = [IA_CFG["titulo_institucion"], INSTITUCION,
              render(IA_CFG["linea_moneda"], moneda=metadata.get("moneda", ""),
                     uit=metadata.get("uit_vigente", ""))]

    partes += ["\n" + IA_CFG["titulo_carreras"]]
    partes += [f"- {_nombre_carrera(v['titulo'])}" for v in carreras_info.values()]

    user_norm = normalizar(texto_usuario)
    tokens = {t for t in user_norm.split() if len(t) > 4}
    pide_detalle = any(p in user_norm for p in PALABRAS_DETALLE)
    detalle = [v for v in carreras_info.values()
               if pide_detalle or any(t in normalizar(v["titulo"]) for t in tokens)]
    if detalle:
        partes.append("\n" + IA_CFG["titulo_detalle"])
        partes += [v["texto"] for v in detalle]

    partes.append("\n" + IA_CFG["titulo_faqs"])
    for f in faqs:
        partes.append(json.dumps({k: v for k, v in f.items() if k != "id" and v},
                                 ensure_ascii=False))

    partes.append("\n" + IA_CFG["titulo_pagos"])
    for p in data.get("conceptos_pago", []):
        partes.append(render(IA_CFG["formato_pago"], concepto=p["concepto"], monto=f"{p['monto']:.2f}"))
    return "\n".join(partes)


# --- Validador: rechaza datos que la IA no pudo sacar del conocimiento ---
_CONOCIMIENTO = {k: data[k] for k in ("metadata", "faqs", "conceptos_pago", "info_carreras") if k in data}
_TEXTO_CONOC = json.dumps(_CONOCIMIENTO, ensure_ascii=False)
_CORPUS = " " + normalizar(_TEXTO_CONOC) + " "
_RE_NUM = re.compile(r"\d+(?:[.,]\d+)*")
CARRERAS_FANTASMA = [normalizar(c) for c in BOT["validador"]["carreras_fantasma"]]


def _valor(token):
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", token):
        token = re.sub(r"[.,]", "", token)
    else:
        token = token.replace(",", ".")
    try:
        return round(float(token), 2)
    except ValueError:
        return None


# Todo número que aparece en el conocimiento (montos, teléfonos, cuenta, leyes, horas, plazos…)
NUMEROS_PERMITIDOS = {v for v in (_valor(t) for t in _RE_NUM.findall(_TEXTO_CONOC)) if v is not None}

# Montos oficiales y sus sumas (1 a 3 conceptos): "2 cursos = 80", "180 + 123 = 303"
_MONTOS = {float(p["monto"]) for p in data.get("conceptos_pago", [])}
for _f in faqs:
    for _t in _RE_NUM.findall(_f.get("costo") or ""):
        _v = _valor(_t)
        if _v:
            _MONTOS.add(_v)
SUMAS_PERMITIDAS = set()
for _n in (1, 2, 3):
    for _c in combinations_with_replacement(sorted(_MONTOS), _n):
        SUMAS_PERMITIDAS.add(round(sum(_c), 2))


def validar_respuesta(resp: str, texto_usuario: str = ""):
    """(es_valida, motivo). Solo vigila carreras inexistentes y cifras sin respaldo."""
    rn = " " + normalizar(resp) + " "
    tn = " " + normalizar(texto_usuario) + " "
    for f in CARRERAS_FANTASMA:
        if f" {f} " in rn and f" {f} " not in _CORPUS:
            # Si el usuario preguntó por ella, permitimos que la IA diga "No la tenemos"
            if f" {f} " in tn:
                continue
            return False, f"carrera inexistente: '{f}'"
    for tok in _RE_NUM.findall(resp):
        v = _valor(tok)
        if v is None or v < 20:
            continue
        if v == int(v) and 2020 <= v <= 2035:
            continue
        if v in NUMEROS_PERMITIDOS or v in SUMAS_PERMITIDAS:
            continue
        return False, f"dato no verificado: {tok}"
    return True, "ok"


def limpiar_salida_ia(t: str) -> str:
    t = t.strip()
    t = re.sub(r"\*\*(.+?)\*\*", r"*\1*", t, flags=re.S)
    t = re.sub(r"(?m)^#{1,6}\s*", "", t)
    t = re.sub(r"\s*\(?\bFAQ-\d+\b\)?", "", t)
    return t.strip()


# ----------------------------------------------------------
# 7. IA (DeepSeek)
# ----------------------------------------------------------
_ia_hist = defaultdict(deque)


def ia_permitida(numero) -> bool:
    ahora, q = time.time(), _ia_hist[numero]
    while q and ahora - q[0] > 60:
        q.popleft()
    if len(q) >= int(AJ["ia_max_por_minuto"]):
        return False
    q.append(ahora)
    return True


def _llamar_ia(system, user, temperature, max_tokens, timeout):
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": DEEPSEEK_MODEL, "temperature": temperature, "max_tokens": max_tokens,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for _ in range(2):
        try:
            r = http.post(DEEPSEEK_URL, headers=headers, json=payload, timeout=timeout)
            r.raise_for_status()
            texto = r.json()["choices"][0]["message"]["content"].strip()
            if texto:
                return texto
        except Exception as e:
            log(f"❌ IA: {e}")
    return None


def get_faq_by_id(faq_id):
    return next((f for f in faqs if f.get("id") == faq_id), None)


def _tiene_costo(faq) -> bool:
    return bool(re.search(r"S/\s*\d", faq.get("costo") or ""))


def formatear_faq(faq) -> str:
    """Versión sin IA (respaldo): sale directo del JSON."""
    partes = [(faq.get("respuesta") or "").strip()]
    reqs = faq.get("requisitos") or []
    if reqs:
        partes.append(ETIQ["requisitos"] + "\n" + "\n".join(f"{ETIQ['vineta']} {r}" for r in reqs))
    if faq.get("costo"):
        partes.append(f"{ETIQ['costo']} {faq['costo']}")
    if faq.get("oficina"):
        partes.append(f"{ETIQ['oficina']} {faq['oficina']}")
    if faq.get("observaciones"):
        partes.append(f"{ETIQ['observaciones']} {faq['observaciones']}")
    if _tiene_costo(faq):
        partes.append(PAGOS["aviso_manual"])
    return "\n\n".join(p for p in partes if p)


def redactar_faq(faq) -> str:
    """La IA solo redacta. Si falla o inventa algo, se envía la ficha tal cual."""
    ficha = json.dumps({k: v for k, v in faq.items() if k != "id" and v}, indent=2, ensure_ascii=False)
    prompt = PROMPT_FAQ.format(institucion=INSTITUCION, ficha=ficha,
                               regla_pago=PAGOS["regla_ia"] if _tiene_costo(faq) else "")
    r = _llamar_ia(prompt, "Redacta el mensaje con la ficha oficial.", 0.2, 900, 25)
    if r:
        r = limpiar_salida_ia(r)
        ok, motivo = validar_respuesta(r)
        if ok:
            return r
        log(f"⚠️ Redacción FAQ rechazada ({motivo}) | {faq.get('id')}")
    return formatear_faq(faq)


def redactar_libre(texto_usuario):
    consulta = texto_usuario[: int(AJ["max_chars_consulta"])]
    prompt = PROMPT_LIBRE.format(institucion=INSTITUCION, contexto=construir_contexto(consulta),
                                 lista_carreras=LISTA_CARRERAS, total_carreras=TOTAL_CARRERAS,
                                 mensaje_sin_dato=MSG["sin_dato"])
    r = _llamar_ia(prompt, consulta, 0.3, 450, 30)
    return limpiar_salida_ia(r) if r else None


def responder_consulta(numero, texto):
    if not ia_permitida(numero):
        enviar_texto(numero, MSG["demasiadas_consultas"])
        return
    r = redactar_libre(texto)
    if r is None:
        enviar_texto(numero, MSG["error_ia"])
        return
    ok, motivo = validar_respuesta(r, texto)
    if not ok:
        log(f"⚠️ IA rechazada ({motivo}) | texto='{texto}' | respuesta='{r}'")
        r = MSG["respuesta_rechazada"]
    enviar_texto(numero, r)


# ----------------------------------------------------------
# 8. MENÚS Y ACCIONES
# ----------------------------------------------------------


def menu_principal(ses, nuevo=False):
    saludo = SALUDOS_MENU["nuevo"] if nuevo else render(SALUDOS_MENU["regreso"], nombre=primer_nombre(ses))
    return render(MENUS["principal"], saludo=saludo)


def ir_menu_principal(numero, ses, nuevo=False):
    ses["estado"] = "menu_principal"
    enviar_texto(numero, menu_principal(ses, nuevo))


def reiniciar_registro(numero, ses, mensaje):
    ses["estado"] = "esperando_nombre"
    ses["datos"] = {k: v for k, v in ses["datos"].items() if k == "fallback_indice"}
    enviar_texto(numero, MSG[mensaje])


def mostrar_faq(numero, ses, faq_id, volver, pie):
    faq = get_faq_by_id(faq_id)
    if not faq:
        enviar_texto(numero, MSG["info_no_encontrada"])
        return
    ses["estado"] = "viendo_info"
    ses["datos"]["estado_anterior"] = volver
    enviar_texto(numero, redactar_faq(faq) + "\n\n" + PIES[pie])


def mostrar_carrera(numero, clave):
    info = carreras_info[clave]
    enviar_imagen(numero, info["url"], info["titulo"])
    pausa()
    enviar_texto(numero, info["texto"])
    enviar_texto(numero, MSG["otra_carrera"])


def enviar_easter_egg(numero, ses):
    imagenes = EASTER["imagenes"]
    enviar_texto(numero, EASTER["mensaje_secreto"])
    pausa()
    i = int(ses["datos"].get("fallback_indice", 0)) % len(imagenes)
    ses["datos"]["fallback_indice"] = (i + 1) % len(imagenes)
    enviar_imagen(numero, imagenes[i]["url"], imagenes[i]["texto"])
    pausa()
    if ses["datos"].get("nombres"):
        enviar_texto(numero, menu_principal(ses))
    else:
        enviar_texto(numero, MSG["recordar_nombre"])


def abrir_opcion_principal(numero, ses, opcion):
    """Opciones del menú principal (también las usan los atajos como 'carreras')."""
    if opcion == "0":
        reiniciar_registro(numero, ses, "reinicio_desde_menu")
    
    # --- SUBMENÚ 1 (Admisión) -> Usamos Botones ---
    elif opcion == "1":
        ses["estado"] = "submenu_1"
        texto = "🎓 *MODALIDADES DE ADMISIÓN*\n¿Qué modalidad te interesa?"
        enviar_botones(numero, texto, [("1", "Ordinario"), ("2", "Exoneración"), ("3", "Extraordinario")])
        
    # --- SUBMENÚ 2 (Costos) -> Usamos Botones ---
    elif opcion == "2":
        ses["estado"] = "submenu_2"
        texto = "💰 *COSTOS DE POSTULAR*\nElige una opción:"
        enviar_botones(numero, texto, [("1", "Inscripción/Prospecto"), ("2", "Matrícula"), ("3", "Cómo pagar")])

    # --- SUBMENÚ 3 (Requisitos) -> FAQ Directa ---
    elif opcion == "3":
        mostrar_faq(numero, ses, DIRECTOS["3"], "menu_principal", "info_directa")
        
    # --- SUBMENÚ 4 (Traslado) -> FAQ Directa ---
    elif opcion == "4":
        mostrar_faq(numero, ses, DIRECTOS["4"], "menu_principal", "info_directa")

    # --- SUBMENÚ 5 (Trámites) -> Usamos Botones ---
    elif opcion == "5":
        ses["estado"] = "submenu_5"
        texto = "📄 *TRÁMITES ACADÉMICOS*\n¿Qué trámite necesitas?"
        enviar_botones(numero, texto, [("1", "Cambio de Turno"), ("2", "Subsanación"), ("3", "Convalidación")])

    # --- SUBMENÚ 6 (Certificados) -> Usamos Botones ---
    elif opcion == "6":
        ses["estado"] = "submenu_6"
        texto = "📑 *CERTIFICADOS Y CONSTANCIAS*\nElige una opción:"
        enviar_botones(numero, texto, [("1", "Cert. de Estudios"), ("2", "Const. Egresado"), ("3", "Duplicado Título")])

    # --- SUBMENÚ 7 (Matrícula) -> Usamos Botones ---
    elif opcion == "7":
        ses["estado"] = "submenu_7"
        texto = "🎓 *MATRÍCULA*\n¿Qué necesitas hacer?"
        enviar_botones(numero, texto, [("1", "Ratificación"), ("2", "Reserva"), ("3", "Licencia")])

    # --- SUBMENÚ 8 (Titulación) -> Usamos Botones ---
    elif opcion == "8":
        ses["estado"] = "submenu_8"
        texto = "🎉 *TITULACIÓN*\n¿Qué modalidad te interesa?"
        enviar_botones(numero, texto, [("1", "TAP"), ("2", "Examen Suficiencia"), ("3", "Requisitos")])

    # --- Opciones Directas (9, 10, 12) ---
    elif opcion in DIRECTOS:
        mostrar_faq(numero, ses, DIRECTOS[opcion], "menu_principal", "info_directa")
        
    # --- OPCIÓN 11: CARRERAS -> Usamos Lista (porque son 7) ---
    elif opcion == "11":
        ses["estado"] = "menu_carreras"
        secciones = [{
            "title": "Nuestras 7 Carreras",
            "rows": [
                {"id": "1", "title": "💼 Administración"},
                {"id": "2", "title": "📊 Contabilidad"},
                {"id": "3", "title": "💻 Desarrollo de Sistemas"},
                {"id": "4", "title": "⚡ Electricidad Industrial"},
                {"id": "5", "title": "🚗 Mecánica Automotriz"},
                {"id": "6", "title": "⚙️ Mecánica de Producción"},
                {"id": "7", "title": "📋 Asistencia Administrativa"}
            ]
        }]
        enviar_lista(numero, "🏫 *NUESTRAS CARRERAS*\nToca el botón de abajo para ver la lista completa.", "Ver Carreras", secciones)

    else:
        enviar_texto(numero, MSG["opcion_invalida_menu"])


# ----------------------------------------------------------
# 9. FLUJO DE CONVERSACIÓN
# ----------------------------------------------------------
ESTADOS_REGISTRO = ("esperando_nombre", "esperando_perfil", "esperando_carrera")


def flujo_registro(numero, texto, cmd, ses):
    estado, datos = ses["estado"], ses["datos"]

    if cmd in COMANDOS_REINICIO:
        reiniciar_registro(numero, ses, "reinicio_registro")
        return

    if estado == "esperando_nombre":
        if nombre_parece_consulta(texto):
            responder_consulta(numero, texto)
            pausa()
            enviar_texto(numero, MSG["recordar_nombre"])
            return
        nombre = validar_nombre(texto)
        if not nombre:
            enviar_texto(numero, MSG["nombre_invalido"])
            return
        datos["nombres"] = nombre
        ses["estado"] = "esperando_perfil"
        enviar_texto(numero, render(MSG["pregunta_perfil"], nombre=nombre.split()[0]))
        return

    if estado == "esperando_perfil":
        if cmd in REGISTRO["perfiles"]:
            datos["perfil"] = REGISTRO["perfiles"][cmd]
            ses["estado"] = "esperando_carrera"
            enviar_texto(numero, MSG["pregunta_carrera"])
        elif len(texto) > LONG_LIBRE or parece_pregunta(texto):
            responder_consulta(numero, texto)
            pausa()
            enviar_texto(numero, MSG["recordar_perfil"])
        else:
            enviar_texto(numero, MSG["perfil_invalido"])
        return

    if estado == "esperando_carrera":
        if cmd in REGISTRO["carreras"]:
            datos["carrera"] = REGISTRO["carreras"][cmd]
            ses["estado"] = "menu_principal"
            guardar_cliente(numero, datos["nombres"], datos.get("perfil"), datos["carrera"])
            enviar_texto(numero, menu_principal(ses, nuevo=True))
        elif len(texto) > LONG_LIBRE or parece_pregunta(texto):
            responder_consulta(numero, texto)
            pausa()
            enviar_texto(numero, MSG["recordar_carrera"])
        else:
            enviar_texto(numero, MSG["carrera_invalida"])


def flujo_inicio(numero, texto, cmd, ses) -> bool:
    """Primer contacto (o primer mensaje tras un reinicio del servidor).
    Devuelve True si el mensaje debe seguir procesándose como menú."""
    cliente = cargar_cliente(numero)
    if cliente:
        ses["datos"].update(cliente)
        ses["estado"] = "menu_principal"
        if cmd.isdigit() or cmd in ATAJOS or es_consulta(texto, cmd):
            return True
        enviar_texto(numero, menu_principal(ses))
        return False
    ses["estado"] = "esperando_nombre"
    if es_consulta(texto, cmd):
        responder_consulta(numero, texto)
        pausa()
    enviar_texto(numero, MSG["bienvenida"])
    return False


def flujo_menus(numero, texto, cmd, ses):
    estado, datos = ses["estado"], ses["datos"]

    if not cmd:  # solo emojis, stickers de texto, etc.
        enviar_texto(numero, MSG["no_texto"])
        return
    if cmd in COMANDOS_MENU:
        ir_menu_principal(numero, ses)
        return
    if es_saludo(cmd):
        enviar_texto(numero, render(MSG["saludo_en_menu"], nombre=primer_nombre(ses)))
        return
    if es_agradecimiento(cmd):
        enviar_texto(numero, MSG["agradecimiento"])
        return
    if cmd in ATAJOS:
        abrir_opcion_principal(numero, ses, ATAJOS[cmd])
        return
    if es_consulta(texto, cmd):
        responder_consulta(numero, texto)
        return

    # ---- menú principal ----
    if estado == "menu_principal":
        if cmd.isdigit():
            abrir_opcion_principal(numero, ses, cmd)
        else:
            enviar_easter_egg(numero, ses)
        return

    # ---- submenús y carreras ----
    if estado in SUBMENUS:
        if cmd == "0":
            ir_menu_principal(numero, ses)
        elif estado == "menu_carreras" and cmd in carreras_info:
            mostrar_carrera(numero, cmd)
        elif cmd in MAPEO_FAQS.get(estado, {}):
            mostrar_faq(numero, ses, MAPEO_FAQS[estado][cmd], estado, "info_submenu")
        elif cmd.isdigit():
            enviar_texto(numero, MSG["opcion_invalida_menu"])
            enviar_texto(numero, SUBMENUS[estado])
        else:
            enviar_easter_egg(numero, ses)
        return

    # ---- leyendo una ficha ----
    if estado == "viendo_info":
        anterior = datos.get("estado_anterior", "menu_principal")
        if cmd == "0":
            if anterior in SUBMENUS:
                ses["estado"] = anterior
                enviar_texto(numero, SUBMENUS[anterior])
            else:
                ir_menu_principal(numero, ses)
        elif cmd in MAPEO_FAQS.get(anterior, {}):
            mostrar_faq(numero, ses, MAPEO_FAQS[anterior][cmd], anterior, "info_submenu")
        elif cmd.isdigit():
            enviar_texto(numero, MSG["opcion_invalida_info"])
        else:
            enviar_easter_egg(numero, ses)
        return

    # ---- estado desconocido (sesión vieja o corrupta): recomponer ----
    if datos.get("nombres"):
        ir_menu_principal(numero, ses)
    else:
        ses["estado"] = "esperando_nombre"
        enviar_texto(numero, MSG["bienvenida"])


def procesar_mensaje(numero, texto):
    texto = (texto or "").strip()
    if not texto:
        return
    ses = obtener_sesion(numero)
    antes = _huella(ses)
    try:
        cmd = limpiar_comando(texto)
        if es_sensible(texto):
            enviar_texto(numero, MSG["sensible"])
            return
        if ses["estado"] == "inicio":
            if not flujo_inicio(numero, texto, cmd, ses):
                return
        if ses["estado"] in ESTADOS_REGISTRO:
            flujo_registro(numero, texto, cmd, ses)
        else:
            flujo_menus(numero, texto, cmd, ses)
    finally:
        if _huella(ses) != antes:
            _guardar_sesion_db(numero, ses)


# ----------------------------------------------------------
# 10. WEBHOOK
# ----------------------------------------------------------
TIPOS_IGNORADOS = {"reaction", "system", "ephemeral", "request_welcome", "unknown"}
_locks, _locks_guard = {}, threading.Lock()
_vistos, _vistos_guard = {}, threading.Lock()


def _lock_de(numero):
    with _locks_guard:
        return _locks.setdefault(numero, threading.Lock())


def _es_duplicado(mensaje_id) -> bool:
    """Meta reenvía el webhook si tardamos en responder: evita contestar dos veces."""
    if not mensaje_id:
        return False
    ahora = time.time()
    with _vistos_guard:
        if len(_vistos) > 2000:
            ventana = float(AJ["ventana_duplicados_segundos"])
            for k in [k for k, t in _vistos.items() if ahora - t > ventana]:
                del _vistos[k]
        if mensaje_id in _vistos:
            return True
        _vistos[mensaje_id] = ahora
        return False


def firma_valida(raw: bytes, cabecera) -> bool:
    if not META_APP_SECRET:
        return True
    if not cabecera or not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(META_APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, cabecera.split("=", 1)[1])


def atender_mensaje(mensaje):
    numero = mensaje.get("from")
    if not numero or _es_duplicado(mensaje.get("id")):
        return
    tipo = mensaje.get("type")
    if tipo in TIPOS_IGNORADOS:
        return
    with _lock_de(numero):
        try:
            texto = ""
            if tipo == "text":
                texto = mensaje["text"]["body"]
            elif tipo == "interactive":
                inter = mensaje.get("interactive", {})
                if inter.get("type") == "button_reply":
                    texto = inter["button_reply"]["id"] # Obtenemos el ID del botón
                elif inter.get("type") == "list_reply":
                    texto = inter["list_reply"]["id"] # Obtenemos el ID de la lista
            
            if texto:
                log(f"📩 ***{numero[-4:]}: {texto}")
                procesar_mensaje(numero, texto)
            else:
                enviar_texto(numero, MSG["no_texto"])
        except Exception:
            log(f"❌ Error en el flujo:\n{traceback.format_exc()}")
            try:
                enviar_texto(numero, MSG["error_general"])
            except Exception:
                pass

def procesar_webhook(body):
    try:
        if body.get("object") != "whatsapp_business_account":
            return
        for entrada in body.get("entry", []):
            for cambio in entrada.get("changes", []):
                for mensaje in (cambio.get("value", {}).get("messages") or []):
                    atender_mensaje(mensaje)
    except Exception:
        log(f"❌ Error procesando webhook:\n{traceback.format_exc()}")


@app.api_route("/", methods=["GET", "HEAD"])
def read_root():
    return {"status": "Tellito Bot está despierto y funcionando!"}


@app.get("/webhook")
async def verify_webhook(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge"))
    return Response(status_code=403)


@app.post("/webhook")
async def recibir_mensaje(request: Request, background_tasks: BackgroundTasks):
    raw = await request.body()
    if not firma_valida(raw, request.headers.get("X-Hub-Signature-256")):
        return Response(status_code=403)
    try:
        body = json.loads(raw)
    except ValueError:
        return Response(content="OK", status_code=200)
    # Se responde 200 de inmediato; el trabajo pesado (IA, pausas) va en segundo plano.
    background_tasks.add_task(procesar_webhook, body)
    return Response(content="OK", status_code=200)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
