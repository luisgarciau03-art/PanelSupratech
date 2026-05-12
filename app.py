"""
Panel de Prospección — Supratech
Sistema de gestión de prospectos para distribuidoras mexicanas de 5-50 empleados.
Inspirado en PanelNioval, adaptado para venta de SaaS.
"""

from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import gspread
from google.oauth2.service_account import Credentials
import os, json, time, threading, secrets, traceback, requests
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
    'prospectos':  os.environ.get('PROSPECTOS_SHEET_ID',  _MASTER_SHEET),
    'llamadas':    os.environ.get('LLAMADAS_SHEET_ID',    _MASTER_SHEET),
    'clientes':    os.environ.get('CLIENTES_SHEET_ID',    _MASTER_SHEET),
    'seguimiento': os.environ.get('SEGUIMIENTO_SHEET_ID', _MASTER_SHEET),
    'mensajes':    os.environ.get('MENSAJES_SHEET_ID',    _MASTER_SHEET),
}

# Si prefieres un solo spreadsheet con múltiples hojas, pon el mismo ID en todos
# y diferencia por nombre de hoja (SHEET_TABS abajo).
SHEET_TABS = {
    'prospectos':  os.environ.get('TAB_PROSPECTOS',  'PROSPECTOS'),
    'llamadas':    os.environ.get('TAB_LLAMADAS',    'LLAMADAS'),
    'clientes':    os.environ.get('TAB_CLIENTES',    'CLIENTES'),
    'seguimiento': os.environ.get('TAB_SEGUIMIENTO', 'SEGUIMIENTO'),
    'mensajes':    os.environ.get('TAB_MENSAJES',    'MENSAJES'),
}

IMGBB_API_KEY   = os.environ.get('IMGBB_API_KEY', '')
GMAPS_API_KEY   = os.environ.get('GOOGLE_PLACES_API_KEY', '') or os.environ.get('GMAPS_API_KEY', '')
TELEGRAM_TOKEN  = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT   = os.environ.get('TELEGRAM_CHAT_ID', '')

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

def _init_headers(ws, key):
    """Inicializa encabezados en hojas nuevas."""
    headers = {
        'prospectos':  ['Nombre', 'Empresa', 'Giro', 'Ciudad', 'Teléfono', 'WhatsApp',
                        'Empleados', 'Estado', 'Origen', 'Fecha', 'Notas'],
        'llamadas':    ['Timestamp', 'Empresa', 'Respondió', 'SKUs', 'Sistema Actual', 'Pedidos/Mes',
                        'Empleados', 'Decisor', 'Interés Demo', 'Agendó Demo', 'Conclusión', 'Notas'],
        'clientes':    ['Fecha', 'Empresa', 'Giro', 'Ciudad', 'Plan', 'Monto MXN', 'Estado', 'Notas'],
        'seguimiento': ['Empresa', 'Estado Pipeline', 'Próxima Acción', 'Fecha Próximo Contacto',
                        'Notas', 'Responsable'],
        'mensajes':    ['Intro Llamada', 'Presentación Supratech', 'Manejo de Objeciones',
                        'Cierre Demo', 'Follow-up WhatsApp', 'No Interesa - Cierre Amable'],
    }
    if key in headers:
        ws.append_row(headers[key])

# ─────────────────────────────────────────────
# CACHÉ (TTL 5 min — igual que Nioval)
# ─────────────────────────────────────────────
_cache    = {}
CACHE_TTL = 300

def get_data(key):
    now = time.time()
    if key in _cache:
        data, ts = _cache[key]
        if now - ts < CACHE_TTL:
            return data
    ws   = get_worksheet(key)
    rows = ws.get_all_values()
    data = values_to_records(rows)
    _cache[key] = (data, now)
    return data

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

def get_prospecto_pendiente(skip=0):
    """Devuelve el siguiente prospecto sin llamada (Estado vacío o 'Por llamar')."""
    data = get_data('prospectos')
    pendientes = [p for p in data if p.get('Estado', '').strip() in ('', 'Por llamar')]
    if skip < len(pendientes):
        return pendientes[skip]
    return None

# ─────────────────────────────────────────────
# IMPORTADOR (Google Maps) — requiere GMAPS_API_KEY
# ─────────────────────────────────────────────
_import_job = {
    'status':    'idle',
    'ciudad':    '',
    'progreso':  0,
    'encontrados': 0,
    'descartados': 0,
    'log':       [],
    'error':     None,
}
_import_lock = threading.Lock()

CATEGORIAS_IMPORTADOR = [
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
]

def _buscar_negocios(gmaps, categoria, ciudad):
    resultados = []
    query = f'{categoria} en {ciudad} Mexico'
    resp  = gmaps.places(query=query)
    ids_vistos = set()
    for place in resp.get('results', []):
        pid = place.get('place_id', '')
        if pid in ids_vistos:
            continue
        ids_vistos.add(pid)
        rating = place.get('rating', 0) or 0
        reviews = place.get('user_ratings_total', 0) or 0
        if rating < 3.0 or reviews < 3:
            continue
        det = gmaps.place(place_id=pid, fields=[
            'name','formatted_phone_number','formatted_address',
            'website','opening_hours','geometry','business_status'
        ]).get('result', {})
        tel = det.get('formatted_phone_number', '')
        if not tel:
            continue
        resultados.append({
            'Nombre':     det.get('name', ''),
            'Ciudad':     ciudad,
            'Giro':       categoria,
            'Teléfono':   tel.replace(' ', '').replace('-', ''),
            'Dirección':  det.get('formatted_address', ''),
            'Sitio Web':  det.get('website', ''),
            'Calificación': rating,
            'Reseñas':    reviews,
            'Fecha':      datetime.now().strftime('%d/%m/%Y'),
        })
    return resultados

def _exportar_a_prospectos(resultados, ciudad):
    ws = get_worksheet('prospectos')
    existing = ws.get_all_values()
    nombres_existentes = {r[0].lower() for r in existing[1:] if r}
    nuevos = [r for r in resultados if r['Nombre'].lower() not in nombres_existentes]
    if nuevos:
        rows = [[
            r['Nombre'], r.get('Empresa', r['Nombre']), r['Giro'], r['Ciudad'],
            r['Teléfono'], '', '', 'Por llamar', 'Importador', r['Fecha'], ''
        ] for r in nuevos]
        ws.append_rows(rows)
    invalidar(['prospectos'])
    return len(nuevos)

def _worker_importador(ciudad):
    global _import_job
    try:
        import googlemaps
        gmaps = googlemaps.Client(key=GMAPS_API_KEY)
        total = 0
        for i, cat in enumerate(CATEGORIAS_IMPORTADOR):
            with _import_lock:
                _import_job.update({'categoria': cat, 'progreso': int(i / len(CATEGORIAS_IMPORTADOR) * 100)})
            res = _buscar_negocios(gmaps, cat, ciudad)
            n   = _exportar_a_prospectos(res, ciudad)
            total += n
            with _import_lock:
                _import_job['encontrados'] = total
                _import_job['log'].append(f'{cat}: {n} nuevos')
        with _import_lock:
            _import_job.update({'status': 'done', 'progreso': 100})
    except Exception as e:
        with _import_lock:
            _import_job.update({'status': 'error', 'error': str(e)})

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
    """Inicializa encabezados en todas las hojas. Visitar una sola vez."""
    HEADERS = {
        'prospectos':  ['Nombre', 'Empresa', 'Giro', 'Ciudad', 'Teléfono', 'WhatsApp',
                        'Empleados', 'Estado', 'Origen', 'Fecha', 'Notas'],
        'llamadas':    ['Timestamp', 'Empresa', 'Respondió', 'SKUs', 'Sistema Actual',
                        'Pedidos/Mes', 'Empleados', 'Decisor', 'Interés Demo',
                        'Agendó Demo', 'Conclusión', 'Notas'],
        'clientes':    ['Fecha', 'Empresa', 'Giro', 'Ciudad', 'Plan', 'Monto MXN',
                        'Estado', 'Notas'],
        'seguimiento': ['Empresa', 'Estado Pipeline', 'Próxima Acción',
                        'Fecha Próximo Contacto', 'Notas', 'Responsable'],
        'mensajes':    ['Intro Llamada', 'Presentación Supratech', 'Manejo de Objeciones',
                        'Cierre Demo', 'Follow-up WhatsApp', 'No Interesa - Cierre Amable'],
    }
    resultados = {}
    for key, headers in HEADERS.items():
        try:
            ws   = get_worksheet(key)
            fila = ws.row_values(1)
            if fila:
                resultados[key] = f'Ya tiene encabezados: {fila}'
            else:
                ws.append_row(headers)
                resultados[key] = f'✓ Encabezados agregados ({len(headers)} columnas)'
        except Exception as e:
            resultados[key] = f'Error: {str(e)}'

    html = '<h2 style="font-family:monospace;padding:20px">Setup Supratech Sheets</h2><ul style="font-family:monospace;padding:20px">'
    for k, v in resultados.items():
        html += f'<li><b>{k}</b>: {v}</li>'
    html += '</ul><p style="font-family:monospace;padding:20px"><a href="/">← Ir al panel</a></p>'
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
        prospectos = get_data('prospectos')
        llamadas   = get_data('llamadas')
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
        return jsonify({'llamadas': get_data('llamadas')})
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
    skip = int(request.args.get('skip', 0))
    p = get_prospecto_pendiente(skip)
    if not p:
        return jsonify({'fin': True})
    return jsonify({'fin': False, 'prospecto': p})

@app.route('/api/formulario/guardar', methods=['POST'])
def api_guardar_llamada():
    try:
        d  = request.get_json() or {}
        ts = datetime.now().strftime('%Y-%m-%d %H:%M')

        # Guardar resultado en hoja LLAMADAS
        ws_ll = get_worksheet('llamadas')
        ws_ll.append_row([
            ts,
            d.get('empresa', ''),
            d.get('respondio', ''),
            d.get('skus', ''),
            d.get('sistema_actual', ''),
            d.get('pedidos_mes', ''),
            d.get('empleados', ''),
            d.get('decisor', ''),
            d.get('interes_demo', ''),
            d.get('agendo_demo', ''),
            d.get('conclusion', ''),
            d.get('notas', ''),
        ])

        # Actualizar estado del prospecto en hoja PROSPECTOS
        row = d.get('_row')
        if row:
            ws_pr = get_worksheet('prospectos')
            nuevo_estado = _conclusion_to_estado(d.get('conclusion', ''))
            sheet_update_row(ws_pr, int(row), {'Estado': nuevo_estado})

        # Si agendó demo → notificar
        if d.get('agendo_demo') == 'Sí':
            notify_telegram(
                f'📅 *Demo agendada*: {d.get("empresa","")} — '
                f'{d.get("ciudad","")} · {d.get("empleados","")} empleados'
            )

        invalidar(['prospectos', 'llamadas'])
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
@app.route('/api/importador/iniciar', methods=['POST'])
def api_importador_iniciar():
    global _import_job
    if not GMAPS_API_KEY:
        return jsonify({'error': 'GMAPS_API_KEY no configurada'}), 400
    with _import_lock:
        if _import_job['status'] == 'running':
            return jsonify({'error': 'Ya hay una importación en curso'}), 400
        ciudad = (request.get_json() or {}).get('ciudad', '').strip()
        if not ciudad:
            return jsonify({'error': 'Ciudad requerida'}), 400
        _import_job = {
            'status': 'running', 'ciudad': ciudad, 'categoria': '',
            'progreso': 0, 'encontrados': 0, 'descartados': 0,
            'log': [], 'error': None,
        }
    threading.Thread(target=_worker_importador, args=(ciudad,), daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/importador/estado')
def api_importador_estado():
    with _import_lock:
        return jsonify(dict(_import_job))

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    app.run(host='0.0.0.0', port=port, debug=False)
