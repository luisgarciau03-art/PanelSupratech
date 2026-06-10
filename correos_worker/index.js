/**
 * Supratech Correos — Cloudflare Worker
 *
 * Envía emails de prospección via Cloudflare Email Service (binding nativo "send_email").
 *
 * Variables de entorno — configurar en:
 *   Cloudflare Dashboard → Workers & Pages → supratech-correos → Settings → Variables
 *
 *   WORKER_SECRET  Clave para autenticar peticiones desde Flask (obligatoria)
 *   FROM_EMAIL     Remitente, ej: ventas@supratech.mx (el dominio debe estar
 *                  onboardeado en Email Sending, ver wrangler.toml)
 *   FROM_NAME      Nombre remitente, ej: Supratech
 *
 * Despliegue:
 *   cd correos_worker
 *   npm install -g wrangler
 *   wrangler login
 *   wrangler email sending enable <tu-dominio>   (una sola vez, habilita el dominio remitente)
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

    const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim();

    try {
      const params = {
        to,
        from: {
          email: env.FROM_EMAIL || 'ventas@supratech.mx',
          name:  env.FROM_NAME  || 'Supratech',
        },
        subject,
        html,
        text,
      };
      if (reply_to) {
        params.replyTo = reply_to;
      }

      const response = await env.EMAIL.send(params);
      return corsHeaders(json({ ok: true, messageId: response.messageId }));
    } catch (err) {
      return corsHeaders(json({ ok: false, error: `${err.code || ''} ${err.message || err}`.trim() }, 502));
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
