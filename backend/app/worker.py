"""Single-process background worker. Run exactly one API worker per deployment."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .core import SessionLocal, logger, settings, utcnow
from .models import Category, Job, JobEvent, Link
from .services import brave_search, extract_links, preview_link


def event(db, job: Job, message: str, level: str = 'info') -> None:
    db.add(JobEvent(job_id=job.id, message=message[:300], level=level))
    db.commit()


def new_job(db, kind: str, category_id: int | None = None, payload: dict | None = None) -> Job:
    job = Job(kind=kind, category_id=category_id, payload=payload or {}, status='queued')
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _preview_batch(db, job: Job, links: list[Link]) -> None:
    if not links:
        return
    # Strict maximum: 5 in-flight preview requests.
    with ThreadPoolExecutor(max_workers=5, thread_name_prefix='radar-check') as pool:
        jobs = {pool.submit(preview_link, link.platform, link.url): link.id for link in links}
        for future in as_completed(jobs):
            link_id = jobs[future]
            try:
                preview = future.result()
                link = db.get(Link, link_id)
                if link:
                    link.status = preview.get('status', 'pending')
                    if preview.get('name'):
                        link.name = preview['name']
                    if preview.get('members') is not None:
                        link.members = preview['members']
                    link.last_checked_at = utcnow()
            except Exception as exc:
                logger.exception('Erro ao validar link %s', link_id)
                job.errors += 1
                event(db, job, f'Falha ao validar link #{link_id}: {type(exc).__name__}', 'warning')
            job.processed += 1
            db.commit()
            event(db, job, f'Validação {job.processed}/{job.total}')


def _discover(db, job: Job) -> None:
    category = db.get(Category, job.category_id)
    if not category:
        raise RuntimeError('Categoria não encontrada')
    if not category.keywords:
        raise RuntimeError('Defina palavras-chave na categoria para buscar')
    websites = {'telegram': 't.me', 'whatsapp': 'chat.whatsapp.com', 'facebook': 'facebook.com/groups'}
    queries = []
    for keyword in category.keywords[:5]:
        for platform in category.platforms:
            if platform not in websites:
                continue
            excludes = ' '.join('-' + word.replace(' ', '') for word in category.excluded_keywords[:5] if word)
            queries.append(f'site:{websites[platform]} "{keyword}" grupos {excludes}'.strip())
    queries = queries[:max(1, min(settings.max_search_requests, 15))]
    job.total = len(queries)
    db.commit()
    event(db, job, f'Iniciando {len(queries)} consultas públicas')
    found: dict[str, tuple[str, str, str]] = {}
    for query in queries:
        try:
            results = brave_search(query, min(settings.max_search_results, 20))
            for result in results:
                title = str(result.get('title', ''))[:200]
                source = str(result.get('url', ''))[:500]
                snippet = str(result.get('description', ''))[:1200]
                for platform, url in extract_links(' '.join([source, title, snippet])):
                    found.setdefault(url, (platform, title, source))
        except Exception as exc:
            logger.exception('Busca falhou: %s', query)
            job.errors += 1
            event(db, job, f'Consulta indisponível: {type(exc).__name__}', 'warning')
        job.processed += 1
        db.commit()
        event(db, job, f'Busca {job.processed}/{len(queries)} concluída')
    category.last_searched_at = utcnow()
    job.total += len(found)
    db.commit()
    event(db, job, f'{len(found)} convites candidatos identificados')
    new_links = []
    for url, (platform, title, source) in found.items():
        try:
            existing = db.scalar(select(Link.id).where(Link.url == url))
            if existing:
                job.duplicates += 1
                job.processed += 1
                db.commit()
                continue
            link = Link(category_id=category.id, platform=platform, url=url,
                        name=title[:200] if title else 'Grupo a revisar', source=source,
                        status='pending')
            db.add(link)
            db.commit()
            db.refresh(link)
            job.added += 1
            new_links.append(link)
        except Exception:
            db.rollback()
            logger.exception('Falha ao salvar candidato')
            job.errors += 1
            job.processed += 1
    db.commit()
    _preview_batch(db, job, new_links)
    event(db, job, f'Importados {job.added} grupos; {job.duplicates} duplicados')


def process_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.status not in ('queued', 'running'):
            return
        job.status = 'running'
        db.commit()
        event(db, job, 'Processamento iniciado')
        try:
            if job.kind == 'search':
                _discover(db, job)
            elif job.kind == 'validate':
                ids = job.payload.get('ids', [])[:100]
                links = list(db.scalars(select(Link).where(Link.id.in_(ids)))) if ids else []
                job.total = len(links)
                db.commit()
                _preview_batch(db, job, links)
            else:
                raise RuntimeError('Tipo de tarefa desconhecido')
            job.status = 'done'
            event(db, job, 'Tarefa concluída')
        except Exception as exc:
            logger.exception('Tarefa %s falhou', job_id)
            db.rollback()
            job = db.get(Job, job_id)
            if job:
                job.status = 'failed'
                job.errors += 1
                event(db, job, f'Falha: {str(exc)[:240]}', 'error')
        finally:
            job = db.get(Job, job_id)
            if job:
                job.finished_at = utcnow()
                db.commit()


_stop = threading.Event()
_thread: threading.Thread | None = None


def worker_loop() -> None:
    with SessionLocal() as db:
        for job in db.scalars(select(Job).where(Job.status == 'running')):
            job.status = 'queued'
        db.commit()
    while not _stop.is_set():
        try:
            with SessionLocal() as db:
                if settings.brave_api_key:
                    now = utcnow()
                    categories = list(db.scalars(select(Category).where(Category.search_enabled.is_(True))))
                    for category in categories:
                        last = category.last_searched_at
                        if last is not None and last.tzinfo is None:
                            last = last.replace(tzinfo=timezone.utc)
                        if last is None or last + timedelta(hours=category.frequency_hours) <= now:
                            active = db.scalar(select(Job.id).where(Job.category_id == category.id, Job.kind == 'search', Job.status.in_(['queued', 'running'])))
                            if not active:
                                new_job(db, 'search', category.id)
                                category.last_searched_at = now
                                db.commit()
                queued_id = db.scalar(select(Job.id).where(Job.status == 'queued').order_by(Job.id).limit(1))
            if queued_id:
                process_job(queued_id)
            else:
                _stop.wait(1)
        except Exception:
            logger.exception('Falha recuperável do worker')
            _stop.wait(3)


def start_worker() -> None:
    global _thread
    _stop.clear()
    if not _thread or not _thread.is_alive():
        _thread = threading.Thread(target=worker_loop, daemon=True, name='radar-worker')
        _thread.start()


def stop_worker() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=3)
