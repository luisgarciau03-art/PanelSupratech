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
]

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
    email = _scrape_page(url, base_domain, timeout)
    if email:
        return email

    # 2. Páginas de contacto
    for path in _CONTACT_PATHS:
        try:
            contact_url = urljoin(url, path)
            email = _scrape_page(contact_url, base_domain, max(4, timeout // 2))
            if email:
                return email
        except Exception:
            continue

    return None


def _scrape_page(url: str, base_domain: str, timeout: int) -> str | None:
    try:
        resp = requests.get(url, timeout=timeout, headers=_HEADERS,
                            allow_redirects=True)
        if resp.status_code != 200:
            return None
        return _parse_emails(resp.text, base_domain)
    except Exception:
        return None


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
