/**
 * Supratech Correos — Cloudflare Worker
 *
 * Envía emails de prospección via Resend (https://resend.com).
 *
 * Variables de entorno — configurar en:
 *   Cloudflare Dashboard → Workers & Pages → supratech-correos → Settings → Variables
 *
 *   WORKER_SECRET    Clave para autenticar peticiones desde Flask (obligatoria)
 *   RESEND_API_KEY   API key de Resend (Settings → API Keys)
 *   FROM_EMAIL       Remitente, ej: ventas@supratech.work (dominio verificado en Resend)
 *   FROM_NAME        Nombre remitente, ej: Supratech
 *
 * Antes del primer despliegue:
 *   1. Crear cuenta en https://resend.com
 *   2. Agregar y verificar el dominio supratech.work (Resend da los registros
 *      DNS — agregarlos en Cloudflare DNS de supratech.work)
 *   3. Generar una API key
 *
 * Despliegue:
 *   cd correos_worker
 *   npm install -g wrangler
 *   wrangler login
 *   wrangler deploy
 */

const RESEND_API_URL = 'https://api.resend.com/emails';

export default {
  async fetch(request, env) {
    if (request.method === 'OPTIONS') {
      return corsHeaders(null, 204);
    }

    if (request.method !== 'POST') {
      return corsHeaders(json({ error: 'Método no permitido' }, 405));
    }

    // Autenticación por Bearer token
    const auth = request.headers.get('Authorization') || '';
    if (!env.WORKER_SECRET || auth !== `Bearer ${env.WORKER_SECRET}`) {
      return corsHeaders(json({ error: 'No autorizado' }, 401));
    }

    if (!env.RESEND_API_KEY) {
      return corsHeaders(json({ error: 'RESEND_API_KEY no configurada' }, 500));
    }

    let body;
    try {
      body = await request.json();
    } catch {
      return corsHeaders(json({ error: 'Body JSON inválido' }, 400));
    }

    const { to, subject, html, reply_to } = body;
    if (!to || !subject || !html) {
      return corsHeaders(json({ error: 'Campos requeridos: to, subject, html' }, 400));
    }

    const fromEmail = env.FROM_EMAIL || 'ventas@supratech.work';
    const fromName  = env.FROM_NAME  || 'Supratech';

    const payload = {
      from:    `${fromName} <${fromEmail}>`,
      to:      [to],
      subject,
      html,
    };
    if (reply_to) {
      payload.reply_to = reply_to;
    }

    try {
      const resp = await fetch(RESEND_API_URL, {
        method:  'POST',
        headers: {
          'Authorization': `Bearer ${env.RESEND_API_KEY}`,
          'Content-Type':  'application/json',
        },
        body: JSON.stringify(payload),
      });

      const data = await resp.json().catch(() => ({}));

      if (resp.ok) {
        return corsHeaders(json({ ok: true, id: data.id }));
      }
      return corsHeaders(json({ ok: false, error: data.message || `HTTP ${resp.status}` }, 502));
    } catch (err) {
      return corsHeaders(json({ ok: false, error: err.message }, 502));
    }
  },
};

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function corsHeaders(response, status) {
  if (!response) {
    return new Response(null, {
      status: status || 204,
      headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
      },
    });
  }
  const headers = new Headers(response.headers);
  headers.set('Access-Control-Allow-Origin', '*');
  return new Response(response.body, { status: response.status, headers });
}
