from __future__ import annotations

import random
import re
import time
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from .core import logger, settings

TG_PATTERN = re.compile(r'(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?(?:\+[A-Za-z0-9_-]{8,}|joinchat/[A-Za-z0-9_-]{8,}|[A-Za-z][A-Za-z0-9_]{3,31})(?:/)?', re.I)
WA_PATTERN = re.compile(r'(?:https?://)?chat\.whatsapp\.com/[A-Za-z0-9]{10,}', re.I)
FB_PATTERN = re.compile(r'(?:https?://)?(?:www\.|m\.)?facebook\.com/groups/[A-Za-z0-9_.-]+/?', re.I)


def normalize_link(raw: str) -> tuple[str, str] | None:
    """Allowlist platforms + paths; strip tracking and external redirects."""
    text = raw.strip().strip('()[]<>.,;\"\'')
    if len(text) > 1000:
        return None
    if not text.lower().startswith(('https://', 'http://')):
        text = 'https://' + text
    try:
        parts = urlsplit(text)
        if parts.username or parts.password or parts.port or parts.scheme not in ('https', 'http'):
            return None
        host = (parts.hostname or '').lower()
        seg = [s for s in parts.path.split('/') if s]
        if host in ('t.me', 'www.t.me', 'telegram.me') and seg:
            if seg[0] == 's' and len(seg) > 1:
                seg = seg[1:]
            if seg[0] == 'joinchat' and len(seg) > 1 and re.fullmatch(r'[\w-]{8,}', seg[1]):
                return ('telegram', 'https://t.me/joinchat/' + seg[1])
            if re.fullmatch(r'\+[\w-]{8,}', seg[0]) or re.fullmatch(r'[A-Za-z][\w]{3,31}', seg[0]):
                return ('telegram', 'https://t.me/' + seg[0])
        if host == 'chat.whatsapp.com' and len(seg) == 1 and re.fullmatch(r'[A-Za-z0-9]{10,}', seg[0]):
            return ('whatsapp', 'https://chat.whatsapp.com/' + seg[0])
        if host in ('www.facebook.com', 'facebook.com', 'm.facebook.com') and len(seg) >= 2 and seg[0] == 'groups' and re.fullmatch(r'[A-Za-z0-9_.-]{2,}', seg[1]):
            return ('facebook', 'https://www.facebook.com/groups/' + seg[1])
    except ValueError:
        return None
    return None


def extract_links(text: str) -> list[tuple[str, str]]:
    found: dict[str, str] = {}
    for pat in (TG_PATTERN, WA_PATTERN, FB_PATTERN):
        for match in pat.finditer(text):
            result = normalize_link(match.group(0))
            if result:
                platform, url = result
                found[url] = platform
    return [(platform, url) for url, platform in found.items()]


def get_with_retry(url: str, headers: dict[str, str], params: dict | None = None, attempts: int = 3) -> httpx.Response:
    # Fixed destinations only. Never send a request to a user-provided host.
    for attempt in range(attempts):
        time.sleep(random.uniform(0.20, 0.75))
        try:
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                response = client.get(url, headers=headers, params=params)
            if response.status_code in (429, 500, 502, 503, 504):
                if attempt + 1 < attempts:
                    time.sleep(min(7, 0.7 * (2 ** attempt) + random.uniform(0, 0.8)))
                    continue
            response.raise_for_status()
            return response
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt + 1 == attempts:
                raise
            time.sleep(min(7, (2 ** attempt) + random.uniform(0, 0.5)))
    raise RuntimeError('Falha de comunicação')


def brave_search(query: str, count: int = 20) -> list[dict]:
    if not settings.brave_api_key:
        raise RuntimeError('Configure BRAVE_API_KEY para realizar buscas na web')
    response = get_with_retry('https://api.search.brave.com/res/v1/web/search',
                              {'X-Subscription-Token': settings.brave_api_key, 'Accept': 'application/json'},
                              {'q': query, 'count': min(count, 20), 'search_lang': 'pt'})
    return response.json().get('web', {}).get('results', [])


def preview_link(platform: str, url: str) -> dict:
    """No Meta/WhatsApp scraping. Telegram: public preview only, never join or authenticate."""
    normalized = normalize_link(url)
    if not normalized or normalized != (platform, url):
        raise ValueError('Link inválido')
    if platform != 'telegram' or '/joinchat/' in url or '/+' in url:
        return {'status': 'pending'}
    try:
        response = get_with_retry(url, {'User-Agent': 'RadarDeGrupos/1.0 (public-preview; no joining)'}, attempts=2)
        html = response.text[:200000]
        soup = BeautifulSoup(html, 'html.parser')
        title_tag = soup.find('meta', attrs={'property': 'og:title'})
        title = title_tag.get('content', '').strip() if title_tag else ''
        extra = soup.select_one('.tgme_page_extra')
        members: int | None = None
        if extra:
            raw = extra.get_text(' ', strip=True).lower()
            m = re.search(r'([\d.,]+)\s+(?:members?|membros?|subscribers?|inscritos?)', raw)
            if m:
                digits = re.sub(r'\D', '', m.group(1))
                # Only unambiguous integers; 1.2K / abbreviated numbers are not exact.
                if digits and ',' not in m.group(1) and ('.' not in m.group(1) or len(m.group(1).split('.')[-1]) == 3):
                    members = int(digits)
        if title:
            return {'status': 'preview', 'name': title[:200], 'members': members}
        return {'status': 'pending'}
    except Exception as exc:
        logger.warning('Prévia Telegram indisponível: %s', type(exc).__name__)
        return {'status': 'blocked'}


def generate_captions(product: str, audience: str, offer_url: str, tone: str, variants: int) -> list[str]:
    if not settings.anthropic_api_key:
        raise RuntimeError('Configure ANTHROPIC_API_KEY para gerar textos por IA')
    prompt = (f'Crie exatamente {variants} legendas ORIGINAIS em português brasileiro, separadas por uma linha contendo somente ###. '
              'Textos curtos, úteis, naturais e diferentes entre si. Não invente preços, prazos, garantias ou escassez. '
              'Não inclua afirmações de que a postagem é permitida; o usuário verificará as regras de cada comunidade. '
              f'Oferta: {product}\nPúblico: {audience}\nTom: {tone}\nLink: {offer_url or "não fornecido"}')
    payload = {'model': settings.anthropic_model, 'max_tokens': 1600,
               'messages': [{'role': 'user', 'content': prompt}]}
    try:
        with httpx.Client(timeout=30) as client:
            res = client.post('https://api.anthropic.com/v1/messages',
                              headers={'x-api-key': settings.anthropic_api_key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json'},
                              json=payload)
        res.raise_for_status()
        output = '\n'.join(c['text'] for c in res.json().get('content', []) if c.get('type') == 'text')
        texts = [x.strip() for x in output.split('###') if x.strip()]
        return texts[:variants] if texts else [output.strip()]
    except httpx.HTTPStatusError as exc:
        logger.warning('Falha Anthropic HTTP %d', exc.response.status_code)
        raise RuntimeError(f'Anthropic retornou HTTP {exc.response.status_code}') from None
    except httpx.RequestError:
        raise RuntimeError('Anthropic indisponível no momento') from None
