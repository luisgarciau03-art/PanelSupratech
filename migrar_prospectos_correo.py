"""
Migración única: copia a PROSPECTOS CORREO los registros de PROSPECTOS que ya
tienen Correo Email (trabajo de enriquecimiento previo, antes de separar los
canales de Llamadas y Correo). No borra ni modifica PROSPECTOS.

Uso: python migrar_prospectos_correo.py
"""
from app import get_worksheet

ws_src = get_worksheet('prospectos')
ws_dst = get_worksheet('prospectos_correo')

rows    = ws_src.get_all_values()
headers = rows[0]
idx     = headers.index('Correo Email')

migrados = [r[:len(headers)] for r in rows[1:] if len(r) > idx and r[idx].strip()]
if migrados:
    ws_dst.append_rows(migrados)

print(f'{len(migrados)} prospectos migrados a PROSPECTOS CORREO')
