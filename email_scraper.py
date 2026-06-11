"""
Extrae emails de contacto del sitio web de un prospecto.
Estrategia: mailto links → /contacto → /contact → regex en texto visible.
"""
import re
import requests
from urllib.parse import urljoin, urlparse, unquote

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

_EMAIL_RE = re.compile(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}')

_SKIP_DOMAINS = {
    'example.com', 'sentry.io', 'wixpress.com', 'googleapis.com',
    'google.com', 'facebook.com', 'instagram.com', 'twitter.com',
    'cloudflare.com', 'w3.org', 'schema.org', 'shopify.com',
    'wix.com', 'wordpress.com',
}

_CONTACT_PATHS = [
    '/contacto', '/contactanos', '/contactanos.html', '/contacto.html',
    '/contact', '/contact-us', '/about', '/nosotros', '/ayuda',
    # El aviso de privacidad es obligatorio por ley en México (LFPDPPP) y
    # casi siempre incluye un correo de contacto del responsable de datos.
    '/aviso-de-privacidad', '/aviso-privacidad', '/politica-de-privacidad',
    '/politica-privacidad', '/privacidad', '/quienes-somos',
]

# Palabras clave para detectar links de contacto/privacidad en la portada,
# por si el sitio usa una ruta no contemplada en _CONTACT_PATHS.
_LINK_KEYWORDS = ('contacto', 'contact', 'privacidad', 'privacy', 'aviso')

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'es-MX,es;q=0.9,en;q=0.8',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


def extract_email_from_website(url: str, timeout: int = 8) -> str | None:
    """
    Devuelve el primer email de contacto encontrado en el sitio web.
    Retorna None si no hay sitio, no responde, o no tiene email visible.
    """
    if not url:
        return None

    url = url.strip()
    if not url.startswith('http'):
        url = 'https://' + url

    base_domain = urlparse(url).netloc.lower().replace('www.', '')

    # 1. Página principal
    home_html = _fetch(url, timeout)
    if home_html:
        email = _parse_emails(home_html, base_domain)
        if email:
            return email
        candidatos = _find_contact_links(home_html, url, base_domain)
    else:
        candidatos = []

    # 2. Links de contacto/privacidad detectados en la portada, luego rutas
    # comunes (aviso de privacidad, contacto, etc.)
    candidatos += [urljoin(url, path) for path in _CONTACT_PATHS]

    vistos = set()
    for contact_url in candidatos:
        if contact_url in vistos:
            continue
        vistos.add(contact_url)
        try:
            html = _fetch(contact_url, max(4, timeout // 2))
            if not html:
                continue
            email = _parse_emails(html, base_domain)
            if email:
                return email
        except Exception:
            continue

    return None


def _fetch(url: str, timeout: int) -> str | None:
    try:
        resp = requests.get(url, timeout=timeout, headers=_HEADERS,
                            allow_redirects=True)
        if resp.status_code != 200:
            return None
        return resp.text
    except Exception:
        return None


def _find_contact_links(html: str, base_url: str, base_domain: str) -> list[str]:
    """Links de la portada cuyo texto o href sugiere contacto/privacidad."""
    if not _BS4:
        return []
    soup = BeautifulSoup(html, 'html.parser')
    links = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        texto = (a.get_text() or '').lower()
        if not any(k in href.lower() or k in texto for k in _LINK_KEYWORDS):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).netloc.lower().replace('www.', '') != base_domain:
            continue
        if full not in links:
            links.append(full)
    return links[:5]


def _parse_emails(html: str, base_domain: str) -> str | None:
    if _BS4:
        return _parse_with_bs4(html, base_domain)
    return _parse_with_regex(html, base_domain)


def _parse_with_bs4(html: str, base_domain: str) -> str | None:
    soup = BeautifulSoup(html, 'html.parser')

    # Prioridad 1: mailto links (más confiable)
    for a in soup.find_all('a', href=True):
        href = a['href']
        if href.lower().startswith('mailto:'):
            email = unquote(href[7:].split('?')[0]).strip().lower()
            if _is_valid(email, base_domain):
                return email

    # Prioridad 2: regex en texto visible (sin scripts/styles)
    for tag in soup(['script', 'style', 'meta', 'link', 'noscript']):
        tag.decompose()
    text = soup.get_text(' ', strip=True)

    for match in _EMAIL_RE.findall(text):
        email = match.lower()
        if _is_valid(email, base_domain):
            return email

    return None


def _parse_with_regex(html: str, base_domain: str) -> str | None:
    # mailto links primero
    mailto_re = re.compile(r'mailto:([^"\'\s>]+)', re.IGNORECASE)
    for match in mailto_re.findall(html):
        email = unquote(match.split('?')[0]).strip().lower()
        if _is_valid(email, base_domain):
            return email

    # Fallback: texto plano
    for match in _EMAIL_RE.findall(html):
        email = match.lower()
        if _is_valid(email, base_domain):
            return email

    return None


def _is_valid(email: str, base_domain: str) -> bool:
    if not email or not _EMAIL_RE.fullmatch(email):
        return False

    parts = email.split('@')
    if len(parts) != 2:
        return False

    local, domain = parts

    # Descartar extensiones de archivo/imagen
    bad_exts = ('.png', '.jpg', '.gif', '.svg', '.webp', '.jpeg',
                '.pdf', '.css', '.js', '.woff', '.ttf', '.otf')
    if any(email.endswith(ext) for ext in bad_exts):
        return False

    domain_clean = domain.lower().replace('www.', '')
    if domain_clean in _SKIP_DOMAINS:
        return False

    if len(local) < 2 or len(domain) < 5:
        return False

    # Evitar emails de plataformas genéricas que no son de la empresa
    generic = ('noreply', 'no-reply', 'donotreply', 'mailer-daemon',
                'postmaster', 'abuse', 'webmaster@wix', 'webmaster@wordpress')
    if any(g in email for g in generic):
        return False

    return True
