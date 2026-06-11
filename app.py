"""
Panel de Prospección — Supratech
Sistema de gestión de prospectos para distribuidoras mexicanas de 5-50 empleados.
Inspirado en PanelNioval, adaptado para venta de SaaS.
"""

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials
import os, json, time, threading, secrets, traceback, requests, html
from datetime import datetime, timedelta
from collections import Counter, defaultdict

app = Flask(__name__)
app.json.sort_keys = False
CORS(app)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(16))

# ─────────────────────────────────────────────
# CONFIGURACIÓN DE SHEETS
# Crear un Google Spreadsheet, compartirlo con maps-905@bubbly-subject-412101.iam.gserviceaccount.com
# y pegar el ID de cada hoja aquí (o via env vars).
# ─────────────────────────────────────────────
_MASTER_SHEET = '1TV4nrtrHlkLPFVhzsMTjrIMvP9mgHkh9MSjiN1a-HR4'

SHEET_IDS = {
    'prospectos':      os.environ.get('PROSPECTOS_SHEET_ID',  _MASTER_SHEET),
    'llamadas_filtro': os.environ.get('LLAMADAS_SHEET_ID',    _MASTER_SHEET),
    'llamadas_pitch':  os.environ.get('LLAMADAS_SHEET_ID',    _MASTER_SHEET),
    'clientes':        os.environ.get('CLIENTES_SHEET_ID',    _MASTER_SHEET),
    'seguimiento':     os.environ.get('SEGUIMIENTO_SHEET_ID', _MASTER_SHEET),
    'mensajes':        os.environ.get('MENSAJES_SHEET_ID',    _MASTER_SHEET),
    'correos_log':     os.environ.get('CORREOS_SHEET_ID',     _MASTER_SHEET),
    'importaciones':   os.environ.get('IMPORTACIONES_SHEET_ID', _MASTER_SHEET),
    'prospectos_correo':    os.environ.get('PROSPECTOS_CORREO_SHEET_ID',    _MASTER_SHEET),
    'importaciones_correo': os.environ.get('IMPORTACIONES_CORREO_SHEET_ID', _MASTER_SHEET),
}

# Si prefieres un solo spreadsheet con múltiples hojas, pon el mismo ID en todos
# y diferencia por nombre de hoja (SHEET_TABS abajo).
SHEET_TABS = {
    'prospectos':       os.environ.get('TAB_PROSPECTOS',       'PROSPECTOS'),
    'llamadas_filtro':  os.environ.get('TAB_LLAMADAS_FILTRO',  'LLAMADAS FILTRO'),
    'llamadas_pitch':   os.environ.get('TAB_LLAMADAS_PITCH',   'LLAMADAS PITCH'),
    'clientes':         os.environ.get('TAB_CLIENTES',         'CLIENTES'),
    'seguimiento':      os.environ.get('TAB_SEGUIMIENTO',      'SEGUIMIENTO'),
    'mensajes':         os.environ.get('TAB_MENSAJES',         'MENSAJES'),
    'correos_log':      os.environ.get('TAB_CORREOS',          'CORREOS LOG'),
    'importaciones':    os.environ.get('TAB_IMPORTACIONES',    'IMPORTACIONES'),
    'prospectos_correo':    os.environ.get('TAB_PROSPECTOS_CORREO',    'PROSPECTOS CORREO'),
    'importaciones_correo': os.environ.get('TAB_IMPORTACIONES_CORREO', 'IMPORTACIONES CORREO'),
}

IMGBB_API_KEY   = os.environ.get('IMGBB_API_KEY', '')
GMAPS_API_KEY   = os.environ.get('GOOGLE_PLACES_API_KEY', '') or os.environ.get('GMAPS_API_KEY', '')
TELEGRAM_TOKEN  = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT   = os.environ.get('TELEGRAM_CHAT_ID', '')

CF_WORKER_URL    = os.environ.get('CF_WORKER_URL', '')
CF_WORKER_SECRET = os.environ.get('CF_WORKER_SECRET', '')
FROM_EMAIL       = os.environ.get('FROM_EMAIL', 'ventas@supratech.mx')
FROM_NAME        = os.environ.get('FROM_NAME', 'Supratech')

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive',
]

# ─────────────────────────────────────────────
# GOOGLE SHEETS CLIENT
# ─────────────────────────────────────────────
_gs_client = None
_gs_lock   = threading.Lock()

def get_gs_client():
    global _gs_client
    with _gs_lock:
        if _gs_client:
            return _gs_client
        raw = os.environ.get('GOOGLE_CREDENTIALS_JSON', '')
        if raw:
            info = json.loads(raw)
            creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        else:
            creds = Credentials.from_service_account_file(
                'bubbly-subject-412101-c969f4a975c5.json', scopes=SCOPES)
        _gs_client = gspread.authorize(creds)
        return _gs_client

def get_worksheet(key):
    """Abre la hoja (tab) correspondiente a la clave."""
    sheet_id = SHEET_IDS.get(key, '')
    tab_name  = SHEET_TABS.get(key, key.upper())
    if not sheet_id:
        raise ValueError(f'SHEET_ID para "{key}" no configurado. Agrega {key.upper()}_SHEET_ID como env var.')
    gc = get_gs_client()
    ss = gc.open_by_key(sheet_id)
    try:
        return ss.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        # Crear hoja si no existe
        ws = ss.add_worksheet(title=tab_name, rows=1000, cols=26)
        _init_headers(ws, key)
        return ws

SHEET_HEADERS = {
    'prospectos':  ['Nombre', 'Empresa', 'Giro', 'Ciudad', 'Teléfono', 'WhatsApp',
                    'Empleados', 'Estado', 'Etapa', 'Origen', 'Fecha', 'Notas',
                    'Nombre Gerente', 'Tel Directo', 'Correo Gerente',
                    'Calificación', 'Reseñas', 'Dirección', 'Sitio Web', 'Maps Link',
                    'Correo Email', 'Email Estado', 'Email Fecha', 'Segmento Email'],
    'llamadas_filtro': ['Timestamp', 'Empresa', 'Ciudad', 'Reseñas',
                        'Respondió', 'Quién Atendió', 'Pasaron con Gerente',
                        'Nombre Gerente', 'Tel Directo', 'Correo Gerente',
                        'Conclusión', 'Notas'],
    'llamadas_pitch':  ['Timestamp', 'Empresa', 'Ciudad', 'Nombre Gerente',
                        'Respondió', 'Tipo Negocio', 'SKUs', 'Sistema Actual',
                        'Pedidos/Mes', 'Empleados', 'Interés Demo', 'Agendó Demo',
                        'Conclusión', 'Notas'],
    'clientes':    ['Fecha', 'Empresa', 'Giro', 'Ciudad', 'Plan', 'Monto MXN', 'Estado', 'Notas'],
    'seguimiento': ['Empresa', 'Estado Pipeline', 'Próxima Acción', 'Fecha Próximo Contacto',
                    'Notas', 'Responsable'],
    'mensajes':    ['Intro Llamada', 'Presentación Supratech', 'Manejo de Objeciones',
                    'Cierre Demo', 'Follow-up WhatsApp', 'No Interesa - Cierre Amable'],
    'correos_log': ['Timestamp', 'Empresa', 'Email', 'Segmento', 'Template',
                    'Asunto', 'Estado', 'Notas'],
    'importaciones': ['Fecha', 'Estado', 'Ciudad', 'Nuevos', 'Total Encontrados'],
}
# Canal de Correo: mismas hojas/esquema que Llamadas, pero completamente
# independientes (sin compartir progreso de importación ni prospectos).
SHEET_HEADERS['prospectos_correo']    = SHEET_HEADERS['prospectos']
SHEET_HEADERS['importaciones_correo'] = SHEET_HEADERS['importaciones']

def _init_headers(ws, key):
    """Inicializa encabezados en hojas nuevas."""
    if key in SHEET_HEADERS:
        ws.append_row(SHEET_HEADERS[key])

# ─────────────────────────────────────────────
# CACHÉ + RATE LIMITING
# TTL: 10 min en prod, devuelve stale en error 429
# Semáforo: máx 2 lecturas simultáneas a Sheets
# ─────────────────────────────────────────────
_cache        = {}
CACHE_TTL     = 600          # 10 minutos
CACHE_STALE   = 3600         # devolver stale hasta 1h si Sheets falla
_sheets_sem   = threading.Semaphore(2)   # max 2 lecturas simultáneas
_key_locks    = {}           # evita lecturas duplicadas por clave
_key_locks_lock = threading.Lock()

def _get_key_lock(key):
    with _key_locks_lock:
        if key not in _key_locks:
            _key_locks[key] = threading.Lock()
        return _key_locks[key]

def _read_sheet_with_backoff(ws, max_retries=3):
    """Lee la hoja con reintentos y backoff exponencial en 429."""
    import gspread.exceptions
    for attempt in range(max_retries):
        try:
            with _sheets_sem:
                return ws.get_all_values()
        except Exception as e:
            msg = str(e)
            if '429' in msg or 'Quota' in msg or 'quota' in msg:
                wait = 2 ** attempt + 1   # 2s, 3s, 5s
                print(f'[SHEETS] 429 quota — esperando {wait}s (intento {attempt+1})')
                time.sleep(wait)
                if attempt == max_retries - 1:
                    raise
            else:
                raise
    return []

def get_data(key):
    now      = time.time()
    key_lock = _get_key_lock(key)

    # Retornar caché fresca sin lock
    if key in _cache:
        data, ts = _cache[key]
        if now - ts < CACHE_TTL:
            return data

    # Solo un hilo por clave lee Sheets a la vez
    with key_lock:
        # Re-check después de adquirir lock (otro hilo pudo haber actualizado)
        if key in _cache:
            data, ts = _cache[key]
            if now - ts < CACHE_TTL:
                return data
        try:
            ws   = get_worksheet(key)
            rows = _read_sheet_with_backoff(ws)
            data = values_to_records(rows)
            _cache[key] = (data, now)
            return data
        except Exception as e:
            # En error 429 u otro: devolver datos stale si existen (hasta 1h)
            if key in _cache:
                data, ts = _cache[key]
                if now - ts < CACHE_STALE:
                    print(f'[CACHE] Devolviendo datos stale para "{key}" — {e}')
                    return data
            raise

def values_to_records(rows):
    if not rows:
        return []
    headers = rows[0]
    records = []
    for i, row in enumerate(rows[1:], start=2):
        rec = {'_row': i}
        for j, h in enumerate(headers):
            rec[h] = row[j] if j < len(row) else ''
        records.append(rec)
    return records

def invalidar(keys):
    for k in keys:
        _cache.pop(k, None)

# ─────────────────────────────────────────────
# HELPERS DE ESCRITURA
# ─────────────────────────────────────────────
def sheet_update_row(ws, row_num, updates: dict):
    """Actualiza celdas específicas de una fila por nombre de columna."""
    headers = ws.row_values(1)
    cells   = []
    for col_name, val in updates.items():
        if col_name.startswith('_'):
            continue
        try:
            col_idx = headers.index(col_name) + 1
            cells.append(gspread.Cell(row_num, col_idx, str(val)))
        except ValueError:
            pass
    if cells:
        ws.update_cells(cells)

def get_prospecto_pendiente(skip=0, etapa='Filtro'):
    """Devuelve el siguiente prospecto pendiente por etapa, ordenado por reseñas."""
    data = get_data('prospectos')
    if etapa == 'Filtro':
        pendientes = [p for p in data if
                      p.get('Estado', '').strip() in ('', 'Por llamar') and
                      p.get('Etapa', 'Filtro') in ('', 'Filtro')]
    else:  # Pitch
        pendientes = [p for p in data if
                      p.get('Estado', '').strip() == 'Contacto obtenido' and
                      p.get('Etapa', '') == 'Pitch']
    pendientes.sort(key=lambda p: int(p.get('Reseñas', 0) or 0), reverse=True)
    if skip < len(pendientes):
        return pendientes[skip]
    return None

# ─────────────────────────────────────────────
# IMPORTADOR (Google Maps) — requiere GMAPS_API_KEY
# ─────────────────────────────────────────────
# Ciudades comerciales/industriales relevantes por estado, para cubrir
# sistemáticamente la importación sin repetir ni saltarse ninguna.
CIUDADES_POR_ESTADO = {
    'Aguascalientes':       ['Aguascalientes', 'Jesús María', 'Calvillo'],
    'Baja California':      ['Tijuana', 'Mexicali', 'Ensenada', 'Tecate'],
    'Baja California Sur':  ['La Paz', 'Los Cabos', 'Ciudad Constitución'],
    'Campeche':             ['Campeche', 'Ciudad del Carmen', 'Champotón'],
    'Chiapas':              ['Tuxtla Gutiérrez', 'Tapachula', 'San Cristóbal de las Casas', 'Comitán'],
    'Chihuahua':            ['Chihuahua', 'Ciudad Juárez', 'Delicias', 'Cuauhtémoc'],
    'Ciudad de México':     ['Ciudad de México'],
    'Coahuila':             ['Saltillo', 'Torreón', 'Monclova', 'Piedras Negras'],
    'Colima':               ['Colima', 'Manzanillo', 'Tecomán'],
    'Durango':              ['Durango', 'Gómez Palacio', 'Lerdo'],
    'Estado de México':     ['Toluca', 'Ecatepec', 'Naucalpan', 'Tlalnepantla', 'Cuautitlán Izcalli'],
    'Guanajuato':           ['León', 'Irapuato', 'Celaya', 'Salamanca', 'Guanajuato'],
    'Guerrero':             ['Acapulco', 'Chilpancingo', 'Iguala', 'Zihuatanejo'],
    'Hidalgo':              ['Pachuca', 'Tulancingo', 'Tula de Allende', 'Tizayuca'],
    'Jalisco':              ['Guadalajara', 'Zapopan', 'Tlaquepaque', 'Puerto Vallarta', 'Tlajomulco'],
    'Michoacán':            ['Morelia', 'Uruapan', 'Zamora', 'Lázaro Cárdenas'],
    'Morelos':              ['Cuernavaca', 'Cuautla', 'Jiutepec'],
    'Nayarit':              ['Tepic', 'Bahía de Banderas', 'Santiago Ixcuintla'],
    'Nuevo León':           ['Monterrey', 'Guadalupe', 'San Nicolás de los Garza', 'Apodaca', 'Santa Catarina'],
    'Oaxaca':               ['Oaxaca de Juárez', 'Salina Cruz', 'Tuxtepec', 'Huajuapan de León'],
    'Puebla':               ['Puebla', 'Tehuacán', 'San Martín Texmelucan', 'Atlixco'],
    'Querétaro':            ['Querétaro', 'San Juan del Río', 'Corregidora', 'El Marqués'],
    'Quintana Roo':         ['Cancún', 'Playa del Carmen', 'Chetumal', 'Cozumel'],
    'San Luis Potosí':      ['San Luis Potosí', 'Soledad de Graciano Sánchez', 'Ciudad Valles', 'Matehuala'],
    'Sinaloa':              ['Culiacán', 'Mazatlán', 'Los Mochis', 'Guasave'],
    'Sonora':               ['Hermosillo', 'Ciudad Obregón', 'Nogales', 'Guaymas'],
    'Tabasco':              ['Villahermosa', 'Cárdenas', 'Comalcalco'],
    'Tamaulipas':           ['Reynosa', 'Matamoros', 'Nuevo Laredo', 'Tampico', 'Ciudad Victoria'],
    'Tlaxcala':             ['Tlaxcala', 'Apizaco', 'Huamantla'],
    'Veracruz':             ['Veracruz', 'Xalapa', 'Coatzacoalcos', 'Córdoba', 'Poza Rica'],
    'Yucatán':              ['Mérida', 'Valladolid', 'Progreso', 'Tizimín'],
    'Zacatecas':            ['Zacatecas', 'Fresnillo', 'Guadalupe'],
}

CATEGORIAS_LLAMADAS = [
    'Ferreterías',
    'Tornillerías',
    'Distribuidoras de materiales de construcción',
    'Distribuidoras de abarrotes',
    'Distribuidoras de consumibles industriales',
    'Refaccionarias autopartes',
    'Mayoristas',
    'Distribuidoras de productos de limpieza',
    'Papelerías mayoristas',
    'Distribuidoras de productos veterinarios',
    'Distribuidoras de plásticos',
    'Negocios de plásticos desechables mayoristas',
    'Farmacias independientes',
    'Distribuidoras farmacéuticas',
    'Distribuidoras de herramientas',
    'Distribuidoras de equipo de seguridad industrial',
    'Distribuidoras de material eléctrico',
    'Distribuidoras de llantas y rines',
    'Distribuidoras de lubricantes y aceites',
    'Distribuidoras de pinturas y recubrimientos',
    'Distribuidoras de telas y textiles al mayoreo',
    'Distribuidoras de calzado al mayoreo',
    'Distribuidoras de juguetes y regalos al mayoreo',
    'Distribuidoras de cosméticos y productos de belleza',
    'Distribuidoras de alimentos congelados',
    'Distribuidoras de equipo médico y consumibles hospitalarios',
    'Distribuidoras de empaques y embalajes',
    'Distribuidoras de productos agropecuarios',
    'Distribuidoras de electrónica y cómputo',
    'Centros de abasto y mayoreo',
    'Supermercados independientes',
    'Tiendas de conveniencia',
    'Vinaterías y licorerías',
    'Mueblerías',
    'Tiendas de electrónica y celulares',
    'Boutiques de ropa y calzado',
    'Jugueterías',
    'Tiendas de regalos y decoración',
    'Tlapalerías',
    'Ópticas',
    'Tiendas de mascotas',
    'Perfumerías',
]

# Canal de Correo: solo categorías cuyo grupo (ver GRUPOS_GIRO) cae en
# _GIROS_EMAIL_FORMALES — son las únicas que _apto_email puede aceptar para
# un prospecto recién importado (frío). Categorías fuera de estos grupos
# nunca generarían un prospecto apto para campaña de email, así que no tiene
# sentido gastar Place Details en ellas.
CATEGORIAS_CORREO = [
    'Ferreterías',
    'Tornillerías',
    'Distribuidoras de herramientas',
    'Distribuidoras de equipo de seguridad industrial',
    'Distribuidoras de consumibles industriales',
    'Distribuidoras de materiales de construcción',
    'Distribuidoras de plásticos',
    'Negocios de plásticos desechables mayoristas',
    'Farmacias independientes',
    'Distribuidoras farmacéuticas',
    'Distribuidoras de productos veterinarios',
    'Supermercados independientes',
    'Tiendas de conveniencia',
    'Vinaterías y licorerías',
    'Mueblerías',
    'Tiendas de electrónica y celulares',
    'Boutiques de ropa y calzado',
    'Jugueterías',
    'Tiendas de regalos y decoración',
    'Tlapalerías',
    'Ópticas',
    'Tiendas de mascotas',
    'Perfumerías',
]

# Dos canales de prospección totalmente independientes: el importador de
# Llamadas no beneficia al de Correo y viceversa — cada uno tiene su propia
# hoja de prospectos, su propio log de importaciones (checklist) y su propia
# lista de categorías.
IMPORT_CHANNELS = {
    'llamadas': {
        'sheet':           'prospectos',
        'log_sheet':       'importaciones',
        'categorias':      CATEGORIAS_LLAMADAS,
        'campo_requerido': 'formatted_phone_number',  # debe tener teléfono
        'min_resenas':     100,
    },
    'correo': {
        'sheet':           'prospectos_correo',
        'log_sheet':       'importaciones_correo',
        'categorias':      CATEGORIAS_CORREO,
        'campo_requerido': 'website',  # debe tener sitio web
        # Las distribuidoras B2B rara vez acumulan muchas reseñas en Google
        # (no son negocios de cara al consumidor) — 300 dejaba casi todo
        # afuera. 200 es un punto medio tras agregar categorías de Retail
        # (supers, tiendas) que sí acumulan reseñas de consumidor final.
        'min_resenas':     200,
    },
}

def _new_import_job():
    return {
        'status':      'idle',
        'estado':      '',
        'ciudad':      '',
        'categoria':   '',
        'progreso':    0,
        'encontrados': 0,
        'descartados': 0,
        'log':         [],
        'error':       None,
    }

_import_jobs  = {canal: _new_import_job() for canal in IMPORT_CHANNELS}
_import_locks = {canal: threading.Lock() for canal in IMPORT_CHANNELS}

def _relevancia(r):
    """Ordena por número de reseñas descendente (más opiniones = más establecido)."""
    try:
        return int(r.get('Reseñas', 0) or 0)
    except:
        return 0

# Place Details con datos de contacto (teléfono/sitio web) se factura en
# el tier "Contact Data" de Google Places — es el costo dominante de la
# importación. Limitamos cuántos candidatos por categoría llegan a pedir
# Details, quedándonos con los de más reseñas (los más establecidos).
MAX_DETALLES_POR_CATEGORIA = 8

# Cadenas nacionales/internacionales y centros comerciales que el Text
# Search devuelve con muchas reseñas (encajan en el filtro min_resenas) pero
# no son el ICP de Supratech (distribuidoras/negocios independientes de 5 a
# 50 empleados). Se filtran por nombre antes de gastar en Place Details.
# Coincidencia por substring, sobre el nombre en minúsculas.
_CADENAS_EXCLUIDAS = [
    # Autoservicio / supermercados
    'walmart', 'bodega aurrera', 'soriana', 'chedraui', 'comercial mexicana',
    'costco', "sam's club", 'sams club', 'h-e-b', 'heb ', 'city market',
    'la comer', 'fresko', 'mercado metropolitano',
    # Conveniencia
    'oxxo', '7-eleven', '7 eleven', 'circle k', 'extra ',
    # Tiendas departamentales / mayoreo
    'liverpool', 'sears', 'sanborns', 'suburbia', 'coppel', 'elektra',
    'famsa', 'office depot', 'office max', 'best buy',
    # Vinaterías / licorerías
    'la europea', 'la castellana',
    # Mueblerías
    'dico', 'crea muebles',
    # Electrónica / cómputo
    'steren', 'radioshack', 'macstore',
    # Ópticas
    'devlyn', 'opticas gmo', 'óptica gmo',
    # Mascotas
    'petco', "petland", "pet's land", 'pets land',
    # Jugueterías / regalos
    'fantasias miguel', 'fantasías miguel', 'juguetron', 'julio cepeda',
    # Centros comerciales / plazas / outlets (no son un solo negocio)
    'plaza mayor', 'centro max', 'plaza del zapato', 'galerias el triunfo',
    'galerías el triunfo', 'factory shops', 'zona piel', 'technology square',
    'plaza calzar y vestir', 'galería del zapato', 'galeria del zapato',
]

def _buscar_negocios(gmaps, categoria, ciudad, nombres_vistos, campo_requerido='formatted_phone_number', min_resenas=100):
    query = f'{categoria} en {ciudad} Mexico'
    resp  = gmaps.places(query=query)

    # Filtrar y deduplicar usando solo datos del Text Search (sin costo
    # adicional) antes de gastar en Place Details.
    candidatos = []
    ids_vistos = set()
    for place in resp.get('results', []):
        pid = place.get('place_id', '')
        if pid in ids_vistos:
            continue
        ids_vistos.add(pid)
        if (place.get('user_ratings_total', 0) or 0) < min_resenas:
            continue
        nombre_lower = place.get('name', '').strip().lower()
        if nombre_lower in nombres_vistos:
            continue
        if any(cadena in nombre_lower for cadena in _CADENAS_EXCLUIDAS):
            continue
        candidatos.append(place)

    # Solo los más relevantes pasan a Place Details
    candidatos.sort(key=lambda p: p.get('user_ratings_total', 0) or 0, reverse=True)
    candidatos = candidatos[:MAX_DETALLES_POR_CATEGORIA]

    resultados = []
    for place in candidatos:
        pid = place['place_id']
        det = gmaps.place(place_id=pid, fields=[
            'name', 'formatted_phone_number', 'formatted_address', 'website', 'url'
        ]).get('result', {})
        if not det.get(campo_requerido):
            continue
        tel = det.get('formatted_phone_number', '')
        nombre = det.get('name', place.get('name', ''))
        nombres_vistos.add(nombre.strip().lower())
        maps_url = det.get('url', f'https://www.google.com/maps/place/?q=place_id:{pid}')
        resultados.append({
            'Nombre':       nombre,
            'Ciudad':       ciudad,
            'Giro':         categoria,
            'Teléfono':     tel.replace(' ', '').replace('-', '') if tel else '',
            'Dirección':    det.get('formatted_address', ''),
            'Sitio Web':    det.get('website', ''),
            'Calificación': place.get('rating', 0) or 0,
            'Reseñas':      place.get('user_ratings_total', 0) or 0,
            'Maps Link':    maps_url,
            'Fecha':        datetime.now().strftime('%d/%m/%Y'),
        })
    resultados.sort(key=_relevancia, reverse=True)
    return resultados

def _exportar_a_prospectos(ws, resultados, ciudad, sheet_key='prospectos'):
    if not resultados:
        return 0
    rows = [[
        r['Nombre'],
        r['Nombre'],
        r['Giro'],
        r['Ciudad'],
        r['Teléfono'],
        '',              # WhatsApp
        '',              # Empleados
        'Por llamar',
        'Filtro',        # Etapa inicial — obtener contacto del gerente
        'Importador Maps',
        r['Fecha'],
        '',              # Notas
        '',              # Nombre Gerente
        '',              # Tel Directo
        '',              # Correo Gerente
        str(r['Calificación']),
        str(r['Reseñas']),
        r['Dirección'],
        r.get('Sitio Web', ''),
        r.get('Maps Link', ''),
    ] for r in resultados]
    ws.append_rows(rows)
    invalidar([sheet_key])
    return len(rows)

def _registrar_importacion(log_sheet_key, estado, ciudad, nuevos, total_encontrados):
    try:
        ws = get_worksheet(log_sheet_key)
        ws.append_row([
            datetime.now().strftime('%d/%m/%Y %H:%M'),
            estado, ciudad, str(nuevos), str(total_encontrados),
        ])
        invalidar([log_sheet_key])
    except Exception:
        pass

def _worker_importador(canal, estado, ciudad):
    cfg = IMPORT_CHANNELS[canal]
    job  = _import_jobs[canal]
    lock = _import_locks[canal]
    try:
        import googlemaps
        gmaps = googlemaps.Client(key=GMAPS_API_KEY)
        ws = get_worksheet(cfg['sheet'])
        existing = ws.get_all_values()
        nombres_vistos = {r[0].strip().lower() for r in existing[1:] if r and r[0]}
        categorias = cfg['categorias']
        total = 0
        total_encontrados = 0
        for i, cat in enumerate(categorias):
            with lock:
                job.update({'categoria': cat, 'progreso': int(i / len(categorias) * 100)})
            res = _buscar_negocios(gmaps, cat, ciudad, nombres_vistos, cfg['campo_requerido'], cfg['min_resenas'])
            n   = _exportar_a_prospectos(ws, res, ciudad, cfg['sheet'])
            total += n
            total_encontrados += len(res)
            with lock:
                job['encontrados'] = total
                job['log'].append(f'{cat}: {n} nuevos')
        _registrar_importacion(cfg['log_sheet'], estado, ciudad, total, total_encontrados)
        with lock:
            job.update({'status': 'done', 'progreso': 100})
    except Exception as e:
        with lock:
            job.update({'status': 'error', 'error': str(e)})

# ─────────────────────────────────────────────
# CORREOS — SEGMENTACIÓN
# ─────────────────────────────────────────────
GRUPOS_GIRO = {
    'Industrial':   ['Ferreterías', 'Tornillerías', 'Distribuidoras de herramientas',
                     'Distribuidoras de equipo de seguridad industrial',
                     'Distribuidoras de consumibles industriales'],
    'Construcción': ['Distribuidoras de materiales de construcción',
                     'Distribuidoras de plásticos',
                     'Negocios de plásticos desechables mayoristas'],
    'Consumo':      ['Distribuidoras de abarrotes',
                     'Distribuidoras de productos de limpieza',
                     'Papelerías mayoristas'],
    'Salud':        ['Farmacias independientes', 'Distribuidoras farmacéuticas',
                     'Distribuidoras de productos veterinarios'],
    'Automotriz':   ['Refaccionarias autopartes', 'Mayoristas'],
    'Retail':       ['Supermercados independientes', 'Tiendas de conveniencia',
                     'Vinaterías y licorerías', 'Mueblerías',
                     'Tiendas de electrónica y celulares', 'Boutiques de ropa y calzado',
                     'Jugueterías', 'Tiendas de regalos y decoración', 'Tlapalerías',
                     'Ópticas', 'Tiendas de mascotas', 'Perfumerías'],
}

_GIRO_TO_GRUPO = {
    giro: grupo
    for grupo, giros in GRUPOS_GIRO.items()
    for giro in giros
}

def _get_grupo(giro: str) -> str:
    return _GIRO_TO_GRUPO.get(giro, 'General')

def _get_tamano(resenas) -> str:
    try:
        n = int(resenas or 0)
    except (ValueError, TypeError):
        n = 0
    if n >= 1000: return 'grande'
    if n >= 300:  return 'mediano'
    return 'pequeño'

def _get_temperatura(etapa: str, estado: str) -> str:
    if estado in ('Interesado', 'Demo agendada'):
        return 'caliente'
    if etapa == 'Pitch' or estado in ('Contacto obtenido', 'Contactado'):
        return 'tibio'
    return 'frío'

# Giros con cultura de correo más formal (administración revisa email,
# cotizaciones/facturas por correo). Fuera de estos, WhatsApp/llamada
# rinde más que el correo frío.
_GIROS_EMAIL_FORMALES = {'Industrial', 'Construcción', 'Salud', 'Retail'}

def _apto_email(grupo: str, tamano: str, temperatura: str, sitio_web: str) -> bool:
    if not (sitio_web or '').strip():
        return False
    # Ya hay relación previa (tibio/caliente) → el correo es seguimiento,
    # aplica sin importar giro o tamaño.
    if temperatura in ('tibio', 'caliente'):
        return True
    # Frío: solo negocios medianos/grandes de giros con cultura de email formal
    return tamano in ('mediano', 'grande') and grupo in _GIROS_EMAIL_FORMALES

def get_segmento(p: dict) -> dict:
    grupo       = _get_grupo(p.get('Giro', ''))
    tamano      = _get_tamano(p.get('Reseñas', 0))
    temperatura = _get_temperatura(p.get('Etapa', 'Filtro'), p.get('Estado', 'Por llamar'))
    return {
        'grupo':       grupo,
        'tamano':      tamano,
        'temperatura': temperatura,
        'apto_email':  _apto_email(grupo, tamano, temperatura, p.get('Sitio Web', '')),
    }

def get_segmento_key(p: dict) -> str:
    seg = get_segmento(p)
    return f"{seg['grupo']}_{seg['temperatura']}"

# ─────────────────────────────────────────────
# CORREOS — TEMPLATES DE EMAIL
# ─────────────────────────────────────────────
_CSS = """<style>
body{font-family:Arial,sans-serif;color:#2b2d42;line-height:1.65;max-width:600px;margin:0 auto;padding:0}
.wrap{padding:32px 28px}
.hdr{border-bottom:3px solid #4361ee;padding-bottom:14px;margin-bottom:22px}
.brand{font-size:1.25em;font-weight:700;color:#4361ee}
.sub{font-size:0.78em;color:#8d99ae;margin-top:2px}
p{margin:0 0 13px}
ul{margin:0 0 14px;padding-left:20px}
li{margin-bottom:5px}
.cta{display:inline-block;background:#4361ee;color:#fff !important;padding:12px 30px;
     border-radius:8px;text-decoration:none;font-weight:700;margin:10px 0 18px}
.ftr{margin-top:30px;border-top:1px solid #e5e7eb;padding-top:14px;
     font-size:0.77em;color:#9ca3af}
</style>"""

EMAIL_TEMPLATES = {
    'frio_industrial': {
        'nombre': 'Frío — Industrial',
        'subject': '¿Cómo controlan el inventario en {empresa}?',
        'html': _CSS + """<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
<p>Hola{nombre_saludo},</p>
<p>Vi que <strong>{empresa}</strong>, en {ciudad}, tiene una sólida presencia en el mercado industrial. Me pongo en contacto desde Supratech porque trabajamos con distribuidoras del mismo giro.</p>
<p>El reto más frecuente que nos cuentan es el <strong>control de inventario</strong>: saber qué entra, qué sale y qué necesita reordenar — sin depender de Excel ni de memoria.</p>
<p>Supratech permite:</p>
<ul>
<li>Ver inventario en tiempo real por almacén</li>
<li>Generar órdenes de compra en segundos</li>
<li>Controlar ventas y clientes desde el celular</li>
</ul>
<p>¿Tienen 15 minutos esta semana para una demo sin compromisos?</p>
<a class="cta" href="https://supratech.mx/demo">Agendar demo gratis</a>
<div class="ftr">Supratech · Software para distribuidoras mexicanas<br>
<small>Para no recibir más correos, responda "BAJA".</small></div>
</div>""",
    },
    'frio_construccion': {
        'nombre': 'Frío — Construcción',
        'subject': '{empresa}: ¿cuántos proveedores manejan sin un sistema centralizado?',
        'html': _CSS + """<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
<p>Hola{nombre_saludo},</p>
<p>Encontramos a <strong>{empresa}</strong> en {ciudad} y nos llamó la atención su presencia en el mercado de materiales de construcción.</p>
<p>En distribuidoras de materiales, el reto habitual es coordinar compras a múltiples proveedores, dar cotizaciones rápidas y mantener el inventario actualizado — todo al mismo tiempo.</p>
<p>Supratech resuelve exactamente eso: un sistema donde manejan compras, ventas, inventario y clientes. Sin hojas de cálculo ni información duplicada.</p>
<a class="cta" href="https://supratech.mx/demo">Ver demo de 15 minutos</a>
<p>¿Le interesaría conocer cómo funciona? Puedo mostrárselo esta semana.</p>
<div class="ftr">Supratech · Software para distribuidoras mexicanas<br>
<small>Para no recibir más correos, responda "BAJA".</small></div>
</div>""",
    },
    'frio_consumo': {
        'nombre': 'Frío — Consumo',
        'subject': 'Control de inventario para distribuidoras como {empresa}',
        'html': _CSS + """<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
<p>Hola{nombre_saludo},</p>
<p>Me pongo en contacto desde Supratech. Encontramos a <strong>{empresa}</strong> en {ciudad} y creemos que podemos ayudarles.</p>
<p>Para distribuidoras de productos de consumo, el reto es claro: manejar alta rotación, controlar la merma y asegurarse de que los pedidos diarios a proveedores sean exactos.</p>
<p>Con Supratech pueden ver en segundos qué producto está por agotarse, qué clientes compran más y generar pedidos automáticos al proveedor — desde una sola pantalla.</p>
<a class="cta" href="https://supratech.mx/demo">Solicitar demo gratuita</a>
<div class="ftr">Supratech · Software para distribuidoras mexicanas<br>
<small>Para no recibir más correos, responda "BAJA".</small></div>
</div>""",
    },
    'frio_salud': {
        'nombre': 'Frío — Salud',
        'subject': 'Trazabilidad y control de lotes para {empresa}',
        'html': _CSS + """<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
<p>Hola{nombre_saludo},</p>
<p>Le escribo desde Supratech. Encontramos a <strong>{empresa}</strong> en {ciudad} y quería presentarles nuestra solución para el sector salud.</p>
<p>En farmacias y distribuidoras farmacéuticas, el control de lotes y fechas de caducidad es crítico. Un error puede traducirse en devoluciones, sanciones o pérdida de clientes.</p>
<p>Supratech ofrece control de inventario con trazabilidad por lote, alertas de caducidad y reportes de movimiento que facilitan cualquier auditoría.</p>
<a class="cta" href="https://supratech.mx/demo">Ver demo — 15 minutos</a>
<div class="ftr">Supratech · Software para distribuidoras mexicanas<br>
<small>Para no recibir más correos, responda "BAJA".</small></div>
</div>""",
    },
    'frio_automotriz': {
        'nombre': 'Frío — Automotriz',
        'subject': '¿Cuántas referencias maneja {empresa}? Supratech las organiza',
        'html': _CSS + """<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
<p>Hola{nombre_saludo},</p>
<p>Me comunico desde Supratech. Vi que <strong>{empresa}</strong> opera en {ciudad} — nos especializamos en software para refaccionarias y distribuidoras automotrices.</p>
<p>El reto más frecuente en el sector: miles de referencias, compatibilidades por año/marca/modelo y clientes que necesitan respuesta inmediata.</p>
<p>Con Supratech buscan refacciones en segundos, ven disponibilidad en tiempo real y generan cotizaciones al momento. Menos tiempo buscando, más ventas cerradas.</p>
<a class="cta" href="https://supratech.mx/demo">Agendar demo gratuita</a>
<div class="ftr">Supratech · Software para distribuidoras mexicanas<br>
<small>Para no recibir más correos, responda "BAJA".</small></div>
</div>""",
    },
    'tibio': {
        'nombre': 'Tibio — Seguimiento',
        'subject': 'Seguimiento — Supratech y {empresa}',
        'html': _CSS + """<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
<p>Hola{nombre_saludo},</p>
<p>Hace unos días intenté comunicarme con <strong>{empresa}</strong> en {ciudad} para presentarles Supratech — el software de operaciones para distribuidoras mexicanas.</p>
<p>Quería hacer un seguimiento por si encontraron un momento. Entiendo que el día a día de una distribuidora es muy ocupado.</p>
<p>Si gustan, puedo agendar una demo de 15 minutos en el horario que más les convenga — sin compromisos y completamente gratuita.</p>
<a class="cta" href="https://supratech.mx/demo">Agendar demo</a>
<p>Quedo al pendiente. Un saludo del equipo Supratech.</p>
<div class="ftr">Supratech · Software para distribuidoras mexicanas<br>
<small>Para no recibir más correos, responda "BAJA".</small></div>
</div>""",
    },
    'caliente': {
        'nombre': 'Caliente — Demo confirmada',
        'subject': 'Confirmación de demo: Supratech × {empresa}',
        'html': _CSS + """<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
<p>Hola{nombre_saludo},</p>
<p>Muchas gracias por confirmar su interés en Supratech. Nos da mucho gusto la oportunidad de mostrarles cómo podemos ayudar a <strong>{empresa}</strong>.</p>
<p>En la demo les mostraremos:</p>
<ul>
<li>Gestión de inventario en tiempo real</li>
<li>Control de pedidos y proveedores</li>
<li>Reportes de ventas y clientes</li>
<li>Acceso desde celular para gerentes</li>
</ul>
<p>Si antes de la demo quieren compartirnos contexto (número de SKUs, proveedores, sistema actual), pueden responder directamente a este correo — nos ayuda a personalizar la presentación.</p>
<p>¡Nos vemos pronto!</p>
<div class="ftr">Supratech · Software para distribuidoras mexicanas</div>
</div>""",
    },
    'frio_general': {
        'nombre': 'Frío — General',
        'subject': '¿Cómo manejan los pedidos en {empresa}?',
        'html': _CSS + """<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
<p>Hola{nombre_saludo},</p>
<p>Me pongo en contacto desde Supratech. Encontramos a <strong>{empresa}</strong> en {ciudad} y quería presentarles brevemente lo que hacemos.</p>
<p>Supratech es un software diseñado para distribuidoras mexicanas de 5 a 50 empleados: control de inventario, gestión de pedidos a proveedores y visibilidad del negocio desde cualquier dispositivo.</p>
<p>¿Tendrían 15 minutos esta semana para una demo? Sin compromiso y completamente gratis.</p>
<a class="cta" href="https://supratech.mx/demo">Solicitar demo</a>
<div class="ftr">Supratech · Software para distribuidoras mexicanas<br>
<small>Para no recibir más correos, responda "BAJA".</small></div>
</div>""",
    },
}

_TEMP_TO_TEMPLATE = {
    'caliente': 'caliente',
    'tibio':    'tibio',
}
_GRUPO_TO_TEMPLATE = {
    'Industrial':   'frio_industrial',
    'Construcción': 'frio_construccion',
    'Consumo':      'frio_consumo',
    'Salud':        'frio_salud',
    'Automotriz':   'frio_automotriz',
    'Retail':       'frio_consumo',
}

def get_template_key(p: dict) -> str:
    seg = get_segmento(p)
    if seg['temperatura'] in _TEMP_TO_TEMPLATE:
        return _TEMP_TO_TEMPLATE[seg['temperatura']]
    return _GRUPO_TO_TEMPLATE.get(seg['grupo'], 'frio_general')

def render_email(template_key: str, p: dict) -> dict:
    tpl = EMAIL_TEMPLATES.get(template_key) or EMAIL_TEMPLATES['frio_general']
    gerente = p.get('Nombre Gerente', '').strip()
    nombre_saludo = f' {gerente}' if gerente else ''
    empresa = p.get('Empresa', p.get('Nombre', 'estimado negocio'))
    ciudad  = p.get('Ciudad', 'su ciudad')

    def apply(s):
        return (s.replace('{empresa}', empresa)
                 .replace('{ciudad}', ciudad)
                 .replace('{nombre_saludo}', nombre_saludo))

    return {'subject': apply(tpl['subject']), 'html': apply(tpl['html'])}

def render_custom_email(subject_raw: str, body_raw: str, p: dict) -> dict:
    """Renderiza un correo redactado a mano desde el panel. El cuerpo es
    texto plano: cada línea no vacía se vuelve un párrafo, envuelto en el
    mismo estilo de marca que las plantillas predefinidas."""
    gerente = p.get('Nombre Gerente', '').strip()
    nombre_saludo = f' {gerente}' if gerente else ''
    empresa = p.get('Empresa', p.get('Nombre', 'estimado negocio'))
    ciudad  = p.get('Ciudad', 'su ciudad')

    def apply(s):
        return (s.replace('{empresa}', empresa)
                 .replace('{ciudad}', ciudad)
                 .replace('{nombre_saludo}', nombre_saludo))

    subject = apply(subject_raw)
    parrafos = ''.join(
        f'<p>{html.escape(apply(linea))}</p>'
        for linea in body_raw.splitlines() if linea.strip()
    )
    body_html = _CSS + f"""<div class="wrap">
<div class="hdr"><div class="brand">Supratech</div><div class="sub">Software para distribuidoras</div></div>
{parrafos}
<div class="ftr">Supratech · Software para distribuidoras mexicanas<br>
<small>Para no recibir más correos, responda "BAJA".</small></div>
</div>"""
    return {'subject': subject, 'html': body_html}

# ─────────────────────────────────────────────
# CORREOS — ENVÍO VIA CLOUDFLARE WORKER
# ─────────────────────────────────────────────
def send_email(to: str, subject: str, html: str) -> tuple:
    """Envía un email via el Cloudflare Worker. Retorna (ok: bool, error: str)."""
    if not CF_WORKER_URL:
        return False, 'CF_WORKER_URL no configurada'
    if not CF_WORKER_SECRET:
        return False, 'CF_WORKER_SECRET no configurada'
    try:
        resp = requests.post(
            CF_WORKER_URL,
            json={'to': to, 'subject': subject, 'html': html},
            headers={
                'Authorization': f'Bearer {CF_WORKER_SECRET}',
                'Content-Type': 'application/json',
            },
            timeout=15,
        )
        if resp.ok:
            return True, ''
        return False, f'HTTP {resp.status_code}: {resp.text[:200]}'
    except Exception as e:
        return False, str(e)

# ─────────────────────────────────────────────
# CORREOS — ENRIQUECIMIENTO DE EMAILS (batch)
# ─────────────────────────────────────────────
_enrich_job  = {'status': 'idle', 'total': 0, 'procesados': 0,
                'encontrados': 0, 'log': [], 'error': None}
_enrich_lock = threading.Lock()

def _worker_enriquecer():
    global _enrich_job
    try:
        from email_scraper import extract_email_from_website
        prospectos = get_data('prospectos_correo')
        pendientes = [p for p in prospectos
                      if p.get('Sitio Web') and not p.get('Correo Email')
                      and get_segmento(p)['apto_email']]
        with _enrich_lock:
            _enrich_job['total'] = len(pendientes)
        ws = get_worksheet('prospectos_correo')
        for i, p in enumerate(pendientes):
            with _enrich_lock:
                _enrich_job['procesados'] = i + 1
            email  = extract_email_from_website(p.get('Sitio Web', ''))
            estado = 'Encontrado' if email else 'Sin email'
            updates = {'Email Estado': estado}
            if email:
                updates['Correo Email'] = email
                with _enrich_lock:
                    _enrich_job['encontrados'] += 1
                    _enrich_job['log'].append(f"{p.get('Empresa','')}: {email}")
            sheet_update_row(ws, p['_row'], updates)
            time.sleep(0.5)
        invalidar(['prospectos_correo'])
        with _enrich_lock:
            _enrich_job['status'] = 'done'
    except Exception as e:
        with _enrich_lock:
            _enrich_job.update({'status': 'error', 'error': str(e)})

# ─────────────────────────────────────────────
# CORREOS — CAMPAÑA (batch send)
# ─────────────────────────────────────────────
_campana_job  = {'status': 'idle', 'total': 0, 'enviados': 0,
                 'errores': 0, 'log': [], 'error': None}
_campana_lock = threading.Lock()

def _worker_campana(targets: list, template_key: str, custom: dict | None = None):
    global _campana_job
    try:
        ws_prosp = get_worksheet('prospectos_correo')
        ws_log   = get_worksheet('correos_log')
        for p in targets:
            email = p.get('Correo Email', '').strip()
            if not email:
                continue
            seg = get_segmento(p)
            if custom:
                rendered = render_custom_email(custom['subject'], custom['body'], p)
            else:
                rendered = render_email(template_key, p)
            ok, err  = send_email(email, rendered['subject'], rendered['html'])
            ts       = datetime.now().strftime('%Y-%m-%d %H:%M')
            estado   = 'Enviado' if ok else 'Error'
            empresa  = p.get('Empresa', p.get('Nombre', ''))
            ws_log.append_row([
                ts, empresa, email,
                f"{seg['grupo']} {seg['temperatura']}",
                template_key, rendered['subject'], estado,
                err if err else '',
            ])
            sheet_update_row(ws_prosp, p['_row'], {
                'Email Estado':   estado,
                'Email Fecha':    ts,
                'Segmento Email': f"{seg['grupo']} {seg['tamano']} {seg['temperatura']}",
            })
            with _campana_lock:
                if ok:
                    _campana_job['enviados'] += 1
                    _campana_job['log'].append(f"✓ {empresa}: {email}")
                else:
                    _campana_job['errores'] += 1
                    _campana_job['log'].append(f"✗ {empresa}: {err[:80]}")
            time.sleep(1)
        invalidar(['prospectos_correo', 'correos_log'])
        with _campana_lock:
            _campana_job['status'] = 'done'
    except Exception as e:
        with _campana_lock:
            _campana_job.update({'status': 'error', 'error': str(e)})

# ─────────────────────────────────────────────
# TELEGRAM NOTIFICACIÓN
# ─────────────────────────────────────────────
def notify_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage',
            json={'chat_id': TELEGRAM_CHAT, 'text': msg, 'parse_mode': 'Markdown'},
            timeout=5
        )
    except:
        pass

# ─────────────────────────────────────────────
# RUTAS PRINCIPALES
# ─────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('panel.html')

@app.route('/formulario')
def formulario():
    return render_template('formulario.html')

# ─────────────────────────────────────────────
# API — REFRESH / DEBUG
# ─────────────────────────────────────────────
@app.route('/setup')
def setup():
    """Inicializa y actualiza encabezados en todas las hojas."""
    resultados = {}
    for key, headers in SHEET_HEADERS.items():
        try:
            ws        = get_worksheet(key)
            fila_act  = ws.row_values(1)
            faltantes = [h for h in headers if h not in fila_act]

            if not fila_act:
                # Hoja vacía — escribir encabezados completos
                ws.append_row(headers)
                resultados[key] = f'✓ {len(headers)} encabezados creados'
            elif faltantes:
                # Agregar columnas que faltan al final
                next_col = len(fila_act) + 1
                cells = [gspread.Cell(1, next_col + i, h) for i, h in enumerate(faltantes)]
                ws.update_cells(cells)
                resultados[key] = f'✓ {len(faltantes)} columnas nuevas agregadas: {faltantes}'
            else:
                resultados[key] = f'✓ Completo ({len(fila_act)} columnas)'
        except Exception as e:
            resultados[key] = f'❌ Error: {str(e)}'

    html = '''<html><body style="font-family:monospace;padding:30px;background:#f8f9ff">
    <h2 style="color:#4361ee">Setup — Supratech Sheets</h2><ul style="line-height:2">'''
    for k, v in resultados.items():
        html += f'<li><b>{k}</b>: {v}</li>'
    html += '</ul><br><a href="/" style="background:#4361ee;color:#fff;padding:10px 20px;border-radius:8px;text-decoration:none">← Ir al panel</a></body></html>'
    return html

@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    data = request.get_json() or {}
    key  = data.get('key', 'all')
    if key == 'all':
        _cache.clear()
    else:
        invalidar([key])
    return jsonify({'ok': True})

@app.route('/api/debug')
def api_debug():
    info = {}
    for key in SHEET_IDS:
        try:
            ws   = get_worksheet(key)
            rows = ws.get_all_values()
            info[key] = {'tab': SHEET_TABS[key], 'rows': len(rows)-1, 'headers': rows[0] if rows else []}
        except Exception as e:
            info[key] = {'error': str(e)}
    return jsonify(info)

# ─────────────────────────────────────────────
# API — PROSPECTOS
# ─────────────────────────────────────────────
@app.route('/api/prospectos/stats')
def api_stats():
    try:
        # Leer secuencialmente para no disparar múltiples requests simultáneos
        prospectos = get_data('prospectos')
        time.sleep(0.3)
        llamadas   = get_data('llamadas')
        time.sleep(0.3)
        clientes   = get_data('clientes')

        total = len(prospectos)
        por_llamar = sum(1 for p in prospectos if p.get('Estado','').strip() in ('','Por llamar'))
        contactados = sum(1 for p in prospectos if p.get('Estado','') == 'Contactado')
        interesados = sum(1 for p in prospectos if p.get('Estado','') in ('Interesado','Demo agendada'))
        convertidos = len(clientes)

        conclusiones = Counter(l.get('Conclusión','') for l in llamadas if l.get('Conclusión'))
        respondio    = Counter(l.get('Respondió','') for l in llamadas if l.get('Respondió'))
        sistemas     = Counter(l.get('Sistema Actual','') for l in llamadas if l.get('Sistema Actual'))
        giros        = Counter(p.get('Giro','') for p in prospectos if p.get('Giro'))
        ciudades     = Counter(p.get('Ciudad','') for p in prospectos if p.get('Ciudad'))

        # Semanas
        por_semana = defaultdict(int)
        for l in llamadas:
            ts = l.get('Timestamp','')
            if ts:
                try:
                    d  = datetime.strptime(ts[:10], '%Y-%m-%d')
                    wk = d.strftime('S%W/%Y')
                    por_semana[wk] += 1
                except:
                    pass
        semanas_sorted = sorted(por_semana.items())[-12:]

        return jsonify({
            'total':       total,
            'por_llamar':  por_llamar,
            'contactados': contactados,
            'interesados': interesados,
            'convertidos': convertidos,
            'conclusiones': dict(conclusiones.most_common(8)),
            'respondio':   dict(respondio.most_common(5)),
            'sistemas':    dict(sistemas.most_common(6)),
            'giros':       dict(giros.most_common(6)),
            'top_ciudades': list(ciudades.most_common(10)),
            'por_semana':  [{'semana': s, 'total': n} for s, n in semanas_sorted],
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prospectos/lista')
def api_prospectos_lista():
    try:
        return jsonify({'prospectos': get_data('prospectos')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prospectos/pendientes')
def api_pendientes():
    try:
        data = get_data('prospectos')
        pend = [p for p in data if p.get('Estado','').strip() in ('','Por llamar')]
        return jsonify({'prospectos': pend, 'total': len(pend)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prospectos/llamadas')
def api_llamadas():
    try:
        etapa = request.args.get('etapa', 'filtro').lower()
        key   = 'llamadas_pitch' if etapa == 'pitch' else 'llamadas_filtro'
        return jsonify({'llamadas': get_data(key), 'etapa': etapa})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prospectos/clientes')
def api_clientes():
    try:
        return jsonify({'clientes': get_data('clientes')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prospectos/agregar', methods=['POST'])
def api_agregar_prospecto():
    try:
        d  = request.get_json() or {}
        ws = get_worksheet('prospectos')
        row = [
            d.get('nombre',''), d.get('empresa',''), d.get('giro',''),
            d.get('ciudad',''), d.get('telefono',''), d.get('whatsapp',''),
            d.get('empleados',''), d.get('estado','Por llamar'),
            d.get('origen','Manual'), datetime.now().strftime('%d/%m/%Y'),
            d.get('notas','')
        ]
        ws.append_row(row)
        invalidar(['prospectos'])
        notify_telegram(f'🆕 *Nuevo prospecto*: {d.get("empresa","")} — {d.get("ciudad","")}')
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/prospectos/actualizar', methods=['POST'])
def api_actualizar_prospecto():
    try:
        d   = request.get_json() or {}
        row = int(d.pop('_row'))
        ws  = get_worksheet('prospectos')
        sheet_update_row(ws, row, d)
        invalidar(['prospectos'])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────
# API — FORMULARIO DE LLAMADAS
# ─────────────────────────────────────────────
@app.route('/api/formulario/siguiente')
def api_siguiente():
    skip  = int(request.args.get('skip', 0))
    etapa = request.args.get('etapa', 'Filtro')
    p = get_prospecto_pendiente(skip, etapa)
    if not p:
        return jsonify({'fin': True})
    return jsonify({'fin': False, 'prospecto': p, 'etapa': etapa})

@app.route('/api/formulario/guardar', methods=['POST'])
def api_guardar_llamada():
    try:
        d  = request.get_json() or {}
        ts = datetime.now().strftime('%Y-%m-%d %H:%M')

        etapa = d.get('etapa', 'Filtro')

        if etapa == 'Filtro':
            ws_ll = get_worksheet('llamadas_filtro')
            ws_ll.append_row([
                ts,
                d.get('empresa', ''),
                d.get('ciudad', ''),
                d.get('resenas', ''),
                d.get('respondio', ''),
                d.get('quien_atendio', ''),
                d.get('pasaron_gerente', ''),
                d.get('nombre_gerente', ''),
                d.get('tel_directo', ''),
                d.get('correo_gerente', ''),
                d.get('conclusion', ''),
                d.get('notas', ''),
            ])
        else:
            ws_ll = get_worksheet('llamadas_pitch')
            ws_ll.append_row([
                ts,
                d.get('empresa', ''),
                d.get('ciudad', ''),
                d.get('nombre_gerente', ''),
                d.get('respondio', ''),
                d.get('tipo_negocio', ''),
                d.get('skus', ''),
                d.get('sistema_actual', ''),
                d.get('pedidos_mes', ''),
                d.get('empleados', ''),
                d.get('interes_demo', ''),
                d.get('agendo_demo', ''),
                d.get('conclusion', ''),
                d.get('notas', ''),
            ])

        # Actualizar prospecto
        row = d.get('_row')
        if row:
            ws_pr = get_worksheet('prospectos')
            updates = {'Estado': _conclusion_to_estado(d.get('conclusion', ''))}
            if etapa == 'Filtro':
                # Si obtuvimos contacto → pasar a etapa Pitch
                if d.get('nombre_gerente') or d.get('tel_directo') or d.get('correo_gerente'):
                    updates['Etapa']          = 'Pitch'
                    updates['Estado']         = 'Contacto obtenido'
                    updates['Nombre Gerente'] = d.get('nombre_gerente', '')
                    updates['Tel Directo']    = d.get('tel_directo', '')
                    updates['Correo Gerente'] = d.get('correo_gerente', '')
                else:
                    updates['Etapa'] = 'Filtro'
            sheet_update_row(ws_pr, int(row), updates)

        # Si agendó demo → notificar
        if d.get('agendo_demo') == 'Sí':
            notify_telegram(
                f'📅 *Demo agendada*: {d.get("empresa","")} — '
                f'{d.get("ciudad","")} · {d.get("empleados","")} empleados'
            )

        invalidar(['prospectos', 'llamadas_filtro', 'llamadas_pitch'])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def _conclusion_to_estado(conclusion):
    mapping = {
        'Demo agendada':              'Demo agendada',
        'Interesado - dar seguimiento': 'Interesado',
        'Enviar info por WhatsApp':   'Contactado',
        'No interesa':                'No interesa',
        'Buzón de voz':               'Buzón',
        'Teléfono incorrecto':        'T. Incorrecto',
    }
    return mapping.get(conclusion, 'Contactado')

# ─────────────────────────────────────────────
# API — SEGUIMIENTO
# ─────────────────────────────────────────────
@app.route('/api/seguimiento')
def api_seguimiento():
    try:
        return jsonify({'seguimiento': get_data('seguimiento')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/seguimiento/update', methods=['POST'])
def api_seguimiento_update():
    try:
        d   = request.get_json() or {}
        row = int(d.pop('_row'))
        ws  = get_worksheet('seguimiento')
        sheet_update_row(ws, row, d)
        invalidar(['seguimiento'])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/seguimiento/agregar', methods=['POST'])
def api_seguimiento_agregar():
    try:
        d  = request.get_json() or {}
        ws = get_worksheet('seguimiento')
        ws.append_row([
            d.get('empresa',''), d.get('estado_pipeline',''),
            d.get('proxima_accion',''), d.get('fecha_proximo',''),
            d.get('notas',''), d.get('responsable','')
        ])
        invalidar(['seguimiento'])
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────
# API — MENSAJES / PLANTILLAS
# ─────────────────────────────────────────────
@app.route('/api/mensajes')
def api_mensajes():
    try:
        ws   = get_worksheet('mensajes')
        rows = ws.get_all_values()
        if not rows:
            return jsonify({'mensajes': []})
        headers  = rows[0]
        contents = rows[1] if len(rows) > 1 else []
        mensajes = []
        for i, h in enumerate(headers):
            if h.strip():
                mensajes.append({
                    'tipo':     h,
                    'contenido': contents[i] if i < len(contents) else '',
                    '_col':     i + 1,
                })
        return jsonify({'mensajes': mensajes})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/mensajes/update', methods=['POST'])
def api_mensajes_update():
    try:
        d   = request.get_json() or {}
        col = int(d.get('_col', 1))
        ws  = get_worksheet('mensajes')
        ws.update_cell(2, col, d.get('contenido', ''))
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────
# API — CLIENTES (conversiones)
# ─────────────────────────────────────────────
@app.route('/api/clientes/agregar', methods=['POST'])
def api_agregar_cliente():
    try:
        d  = request.get_json() or {}
        ws = get_worksheet('clientes')
        ws.append_row([
            datetime.now().strftime('%d/%m/%Y'),
            d.get('empresa',''), d.get('giro',''), d.get('ciudad',''),
            d.get('plan','Mensual'), d.get('monto','399'),
            d.get('estado','Activo'), d.get('notas','')
        ])
        invalidar(['clientes'])
        notify_telegram(
            f'🎉 *¡Nuevo cliente!* {d.get("empresa","")} — {d.get("plan","Mensual")} '
            f'${d.get("monto","399")} MXN/mes'
        )
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────
# API — IMPORTADOR (Google Maps)
# ─────────────────────────────────────────────
@app.route('/api/importador/<canal>/iniciar', methods=['POST'])
def api_importador_iniciar(canal):
    if canal not in IMPORT_CHANNELS:
        return jsonify({'error': 'Canal inválido'}), 404
    if not GMAPS_API_KEY:
        return jsonify({'error': 'GMAPS_API_KEY no configurada'}), 400
    lock = _import_locks[canal]
    with lock:
        if _import_jobs[canal]['status'] == 'running':
            return jsonify({'error': 'Ya hay una importación en curso'}), 400
        data   = request.get_json() or {}
        estado = (data.get('estado') or '').strip()
        ciudad = (data.get('ciudad') or '').strip()
        if not estado or not ciudad:
            return jsonify({'error': 'Estado y ciudad requeridos'}), 400
        _import_jobs[canal] = {
            'status': 'running', 'estado': estado, 'ciudad': ciudad, 'categoria': '',
            'progreso': 0, 'encontrados': 0, 'descartados': 0,
            'log': [], 'error': None,
        }
    threading.Thread(target=_worker_importador, args=(canal, estado, ciudad), daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/importador/<canal>/estado')
def api_importador_estado(canal):
    if canal not in IMPORT_CHANNELS:
        return jsonify({'error': 'Canal inválido'}), 404
    with _import_locks[canal]:
        return jsonify(dict(_import_jobs[canal]))

@app.route('/api/importador/<canal>/checklist')
def api_importador_checklist(canal):
    if canal not in IMPORT_CHANNELS:
        return jsonify({'error': 'Canal inválido'}), 404
    importaciones = get_data(IMPORT_CHANNELS[canal]['log_sheet'])
    hechas = {}
    for r in importaciones:
        key = (r.get('Estado', '').strip(), r.get('Ciudad', '').strip())
        hechas[key] = {
            'fecha':  r.get('Fecha', ''),
            'nuevos': r.get('Nuevos', ''),
            'total':  r.get('Total Encontrados', ''),
        }
    estados = []
    for estado, ciudades in CIUDADES_POR_ESTADO.items():
        lista = []
        for ciudad in ciudades:
            info = hechas.get((estado, ciudad))
            lista.append({
                'ciudad':    ciudad,
                'importado': info is not None,
                'fecha':     info['fecha']  if info else '',
                'nuevos':    info['nuevos'] if info else '',
            })
        estados.append({
            'estado':   estado,
            'pendientes': sum(1 for c in lista if not c['importado']),
            'total':    len(lista),
            'ciudades': lista,
        })
    return jsonify({'estados': estados})

# ─────────────────────────────────────────────
# API — CORREOS
# ─────────────────────────────────────────────
@app.route('/api/correos/stats')
def api_correos_stats():
    try:
        prospectos = get_data('prospectos_correo')
        total      = len(prospectos)
        con_email  = sum(1 for p in prospectos if p.get('Correo Email'))
        sin_email  = total - con_email
        enviados   = sum(1 for p in prospectos if p.get('Email Estado') == 'Enviado')

        segmentos = {}
        aptos          = 0
        aptos_con_email = 0
        for p in prospectos:
            seg   = get_segmento(p)
            skey  = f"{seg['grupo']}_{seg['temperatura']}"
            tkey  = get_template_key(p)
            nombre = f"{seg['grupo']} · {seg['temperatura'].capitalize()}"
            if skey not in segmentos:
                segmentos[skey] = {
                    'nombre': nombre, 'grupo': seg['grupo'],
                    'temperatura': seg['temperatura'], 'template_key': tkey,
                    'con_email': 0, 'sin_email': 0, 'total': 0, 'aptos': 0,
                }
            segmentos[skey]['total'] += 1
            if p.get('Correo Email'):
                segmentos[skey]['con_email'] += 1
            else:
                segmentos[skey]['sin_email'] += 1
            if seg['apto_email']:
                segmentos[skey]['aptos'] += 1
                aptos += 1
                if p.get('Correo Email'):
                    aptos_con_email += 1

        return jsonify({'total': total, 'con_email': con_email,
                        'sin_email': sin_email, 'enviados': enviados,
                        'aptos': aptos, 'aptos_con_email': aptos_con_email,
                        'segmentos': segmentos})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/correos/templates')
def api_correos_templates():
    return jsonify({k: {'nombre': v['nombre'], 'subject': v['subject']}
                    for k, v in EMAIL_TEMPLATES.items()})

@app.route('/api/correos/lista')
def api_correos_lista():
    """Lista cruda de prospectos_correo para revisión/depuración."""
    try:
        prospectos = get_data('prospectos_correo')
        out = []
        for p in prospectos:
            seg = get_segmento(p)
            out.append({
                '_row':         p.get('_row'),
                'empresa':      p.get('Empresa', p.get('Nombre', '')),
                'giro':         p.get('Giro', ''),
                'grupo':        seg['grupo'],
                'ciudad':       p.get('Ciudad', ''),
                'sitio_web':    p.get('Sitio Web', ''),
                'resenas':      p.get('Reseñas', ''),
                'correo_email': p.get('Correo Email', ''),
                'email_estado': p.get('Email Estado', ''),
                'apto_email':   seg['apto_email'],
            })
        return jsonify({'total': len(out), 'prospectos': out})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/correos/enriquecer', methods=['POST'])
def api_correos_enriquecer_uno():
    """Enriquece el email de un único prospecto por _row."""
    try:
        from email_scraper import extract_email_from_website
        d    = request.get_json() or {}
        row  = int(d.get('_row', 0))
        url  = d.get('sitio_web', '')
        if not row or not url:
            return jsonify({'error': '_row y sitio_web requeridos'}), 400
        email  = extract_email_from_website(url)
        ws     = get_worksheet('prospectos_correo')
        estado = 'Encontrado' if email else 'Sin email'
        sheet_update_row(ws, row, {'Correo Email': email or '', 'Email Estado': estado})
        invalidar(['prospectos_correo'])
        return jsonify({'ok': True, 'email': email, 'estado': estado})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/correos/enriquecer-batch', methods=['POST'])
def api_correos_enriquecer_batch():
    global _enrich_job
    with _enrich_lock:
        if _enrich_job['status'] == 'running':
            return jsonify({'error': 'Ya hay un enriquecimiento en curso'}), 400
        _enrich_job = {'status': 'running', 'total': 0, 'procesados': 0,
                       'encontrados': 0, 'log': [], 'error': None}
    threading.Thread(target=_worker_enriquecer, daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/correos/estado-enriquecimiento')
def api_correos_estado_enriquecimiento():
    with _enrich_lock:
        return jsonify(dict(_enrich_job))

@app.route('/api/correos/campana', methods=['POST'])
def api_correos_campana():
    global _campana_job
    with _campana_lock:
        if _campana_job['status'] == 'running':
            return jsonify({'error': 'Ya hay una campaña en curso'}), 400
    d              = request.get_json() or {}
    segmento_key   = d.get('segmento_key', '')
    template_key   = d.get('template_key', '')
    custom_subject = (d.get('custom_subject') or '').strip()
    custom_body    = (d.get('custom_body') or '').strip()
    custom = None
    if custom_subject and custom_body:
        custom = {'subject': custom_subject, 'body': custom_body}
        template_key = 'personalizado'
    elif not template_key or template_key not in EMAIL_TEMPLATES:
        return jsonify({'error': 'template_key inválido'}), 400
    prospectos = get_data('prospectos_correo')
    # Distintos listados de Maps (ej. dos sucursales) pueden compartir el
    # mismo correo de contacto. Si ESE correo ya recibió un envío en
    # cualquier fila/campaña previa, no se vuelve a contactar — sin importar
    # si la fila duplicada en particular sigue marcada como pendiente.
    enviados_emails = {
        p['Correo Email'].strip().lower()
        for p in prospectos
        if p.get('Email Estado') == 'Enviado' and p.get('Correo Email')
    }
    if segmento_key:
        targets = [p for p in prospectos
                   if p.get('Correo Email')
                   and p.get('Email Estado') != 'Enviado'
                   and p['Correo Email'].strip().lower() not in enviados_emails
                   and get_segmento_key(p) == segmento_key
                   and get_segmento(p)['apto_email']]
    else:
        targets = [p for p in prospectos
                   if p.get('Correo Email') and p.get('Email Estado') != 'Enviado'
                   and p['Correo Email'].strip().lower() not in enviados_emails
                   and get_segmento(p)['apto_email']]
    # Deduplicar por email dentro de este mismo envío (mismo motivo: ramas
    # distintas comparten correo y no deben recibir 2 veces en un solo run).
    vistos = set()
    unicos = []
    for p in targets:
        email = p['Correo Email'].strip().lower()
        if email in vistos:
            continue
        vistos.add(email)
        unicos.append(p)
    targets = unicos
    if not targets:
        return jsonify({'error': 'Sin prospectos con email para este segmento'}), 400
    with _campana_lock:
        _campana_job = {'status': 'running', 'total': len(targets),
                        'enviados': 0, 'errores': 0, 'log': [], 'error': None}
    threading.Thread(target=_worker_campana,
                     args=(targets, template_key, custom), daemon=True).start()
    return jsonify({'ok': True, 'total': len(targets)})

@app.route('/api/correos/estado-campana')
def api_correos_estado_campana():
    with _campana_lock:
        return jsonify(dict(_campana_job))

@app.route('/api/correos/log')
def api_correos_log():
    try:
        return jsonify({'log': get_data('correos_log')})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)
