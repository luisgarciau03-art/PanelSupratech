/**
 * Supratech Correos — Cloudflare Worker
 *
 * Envía emails de prospección via MailChannels API.
 *
 * Variables de entorno — configurar en:
 *   Cloudflare Dashboard → Workers & Pages → supratech-correos → Settings → Variables
 *
 *   WORKER_SECRET        Clave para autenticar peticiones desde Flask (obligatoria)
 *   FROM_EMAIL           Remitente, ej: ventas@supratech.mx
 *   FROM_NAME            Nombre remitente, ej: Supratech
 *   MAILCHANNELS_API_KEY API key de MailChannels (requerida para envío real)
 *
 * Despliegue:
 *   cd correos_worker
 *   npm install -g wrangler
 *   wrangler login
 *   wrangler deploy
 */

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

    // Construir payload para MailChannels
    const payload = {
      personalizations: [{ to: [{ email: to }] }],
      from: {
        email: env.FROM_EMAIL || 'ventas@supratech.mx',
        name:  env.FROM_NAME  || 'Supratech',
      },
      subject,
      content: [{ type: 'text/html', value: html }],
    };

    if (reply_to) {
      payload.reply_to = { email: reply_to };
    }

    const mcHeaders = { 'content-type': 'application/json' };
    if (env.MAILCHANNELS_API_KEY) {
      mcHeaders['x-api-key'] = env.MAILCHANNELS_API_KEY;
    }

    try {
      const mc = await fetch('https://api.mailchannels.net/tx/v1/send', {
        method:  'POST',
        headers: mcHeaders,
        body:    JSON.stringify(payload),
      });

      let detail = '';
      try { detail = await mc.text(); } catch {}

      if (mc.status >= 200 && mc.status < 300) {
        return corsHeaders(json({ ok: true }));
      }
      return corsHeaders(json({ ok: false, status: mc.status, detail: detail.slice(0, 300) }, 502));
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
