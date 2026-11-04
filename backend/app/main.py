from __future__ import annotations

import asyncio
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .core import Base, SessionLocal, auth_session, engine, get_db, logger, require_csrf, serializer, settings, utcnow
from .models import Caption, Category, Job, JobEvent, Link, Post
from .schemas import CaptionIn, CaptionOut, CategoryIn, CategoryOut, GenerateIn, LinkImport, LinkOut, LinkPatch, PostIn
from .services import generate_captions, normalize_link
from .worker import new_job, start_worker, stop_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    if os.getenv('RADAR_DISABLE_WORKER') != '1':
        start_worker()
    yield
    stop_worker()


app = FastAPI(title='Radar de Grupos', version='1.0.0', lifespan=lifespan)


@app.get('/api/health')
def health():
    return {'ok': True, 'version': '1.0.0'}


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


_attempts: dict[str, list[datetime]] = {}


@app.post('/api/auth/login')
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else 'local'
    now = utcnow()
    recent = [x for x in _attempts.get(ip, []) if (now - x).total_seconds() < 900]
    if len(recent) >= 5:
        raise HTTPException(429, 'Muitas tentativas. Tente novamente em 15 minutos.')
    valid_user = hmac.compare_digest(body.username.encode(), settings.admin_username.encode())
    valid_pwd = hmac.compare_digest(hashlib.sha256(body.password.encode()).digest(), hashlib.sha256(settings.admin_password.encode()).digest())
    if not (valid_user and valid_pwd):
        _attempts[ip] = recent + [now]
        raise HTTPException(401, 'Usuário ou senha incorretos')
    _attempts.pop(ip, None)
    csrf = secrets.token_urlsafe(24)
    token = serializer.dumps({'user': settings.admin_username, 'csrf': csrf})
    response.set_cookie('radar_session', token, max_age=604800, httponly=True, secure=settings.secure_cookies,
                        samesite='strict', path='/')
    return {'username': settings.admin_username, 'csrf': csrf}


@app.get('/api/auth/me')
def me(session=Depends(auth_session)):
    return {'username': session['user'], 'csrf': session['csrf']}


@app.post('/api/auth/logout')
def logout(response: Response, _=Depends(require_csrf)):
    response.delete_cookie('radar_session', path='/')
    return {'ok': True}


def get_category(db: Session, category_id: int) -> Category:
    category = db.get(Category, category_id)
    if not category:
        raise HTTPException(404, 'Categoria não encontrada')
    return category


def get_link(db: Session, link_id: int) -> Link:
    link = db.get(Link, link_id)
    if not link:
        raise HTTPException(404, 'Grupo não encontrado')
    return link


@app.get('/api/categories', response_model=list[CategoryOut])
def list_categories(_=Depends(auth_session), db: Session = Depends(get_db)):
    return list(db.scalars(select(Category).order_by(Category.name)))


@app.post('/api/categories', response_model=CategoryOut, status_code=201)
def create_category(data: CategoryIn, _=Depends(require_csrf), db: Session = Depends(get_db)):
    category = Category(**data.model_dump())
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Já existe categoria com esse nome') from None
    db.refresh(category)
    return category


@app.put('/api/categories/{category_id}', response_model=CategoryOut)
def update_category(category_id: int, data: CategoryIn, _=Depends(require_csrf), db: Session = Depends(get_db)):
    category = get_category(db, category_id)
    for key, value in data.model_dump().items():
        setattr(category, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, 'Nome já utilizado') from None
    db.refresh(category)
    return category


@app.delete('/api/categories/{category_id}')
def delete_category(category_id: int, _=Depends(require_csrf), db: Session = Depends(get_db)):
    category = get_category(db, category_id)
    for link in db.scalars(select(Link).where(Link.category_id == category_id)):
        link.category_id = None
    for cap in db.scalars(select(Caption).where(Caption.category_id == category_id)):
        cap.category_id = None
    for job in db.scalars(select(Job).where(Job.category_id == category_id)):
        job.category_id = None
    db.delete(category)
    db.commit()
    return {'ok': True}


@app.get('/api/links')
def list_links(q: str = Query('', max_length=150), category_id: int | None = None,
               platform: str | None = None, status: str | None = None, permission: str | None = None,
               favorite: bool = False, archived: bool = False,
               page: int = Query(1, ge=1), page_size: int = Query(24, ge=1, le=100),
               _=Depends(auth_session), db: Session = Depends(get_db)):
    stmt = select(Link).where(Link.archived.is_(archived))
    if q:
        stmt = stmt.where(Link.name.ilike(f'%{q}%') | Link.url.ilike(f'%{q}%') | Link.description.ilike(f'%{q}%'))
    if category_id:
        stmt = stmt.where(Link.category_id == category_id)
    if platform:
        stmt = stmt.where(Link.platform == platform)
    if status:
        stmt = stmt.where(Link.status == status)
    if permission:
        stmt = stmt.where(Link.permission == permission)
    if favorite:
        stmt = stmt.where(Link.favorite.is_(True))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    links = list(db.scalars(stmt.order_by(Link.created_at.desc(), Link.id.desc()).offset((page - 1) * page_size).limit(page_size)))
    return {'items': [LinkOut.model_validate(link).model_dump(mode='json') for link in links], 'total': total, 'page': page, 'page_size': page_size}


@app.post('/api/links/import')
def import_links(data: LinkImport, _=Depends(require_csrf), db: Session = Depends(get_db)):
    if data.category_id is not None:
        get_category(db, data.category_id)
    ids, duplicates, invalid = [], [], []
    for raw in data.urls:
        result = normalize_link(raw)
        if not result:
            invalid.append(raw[:120])
            continue
        platform, url = result
        if db.scalar(select(Link.id).where(Link.url == url)):
            duplicates.append(url)
            continue
        link = Link(category_id=data.category_id, platform=platform, url=url)
        db.add(link)
        db.flush()
        ids.append(link.id)
    db.commit()
    job_id = new_job(db, 'validate', payload={'ids': ids}).id if ids else None
    return {'imported': len(ids), 'duplicates': duplicates, 'invalid': invalid, 'job_id': job_id}


@app.patch('/api/links/{link_id}', response_model=LinkOut)
def update_link(link_id: int, data: LinkPatch, _=Depends(require_csrf), db: Session = Depends(get_db)):
    link = get_link(db, link_id)
    changes = data.model_dump(exclude_unset=True)
    if 'category_id' in changes and changes['category_id'] is not None:
        get_category(db, changes['category_id'])
    for key, val in changes.items():
        setattr(link, key, val)
    db.commit()
    db.refresh(link)
    return link


@app.delete('/api/links/{link_id}')
def delete_link(link_id: int, _=Depends(require_csrf), db: Session = Depends(get_db)):
    link = get_link(db, link_id)
    for post in db.scalars(select(Post).where(Post.link_id == link_id)):
        db.delete(post)
    db.delete(link)
    db.commit()
    return {'ok': True}


@app.post('/api/links/{link_id}/validate')
def validate_link(link_id: int, _=Depends(require_csrf), db: Session = Depends(get_db)):
    get_link(db, link_id)
    return {'job_id': new_job(db, 'validate', payload={'ids': [link_id]}).id}


@app.post('/api/jobs/search/{category_id}')
def search_category(category_id: int, _=Depends(require_csrf), db: Session = Depends(get_db)):
    cat = get_category(db, category_id)
    if not cat.keywords:
        raise HTTPException(422, 'Adicione ao menos uma palavra-chave à categoria')
    return {'job_id': new_job(db, 'search', category_id=category_id).id}


@app.get('/api/jobs/{job_id}')
def get_job(job_id: int, _=Depends(auth_session), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, 'Tarefa não encontrada')
    return {'id': job.id, 'kind': job.kind, 'status': job.status, 'total': job.total,
            'processed': job.processed, 'added': job.added, 'duplicates': job.duplicates,
            'errors': job.errors, 'events': [
                {'id': x.id, 'message': x.message, 'level': x.level}
                for x in db.scalars(select(JobEvent).where(JobEvent.job_id == job_id).order_by(JobEvent.id.desc()).limit(30))][::-1]}


@app.get('/api/jobs/{job_id}/stream')
async def stream_job(job_id: int, _=Depends(auth_session)):
    with SessionLocal() as db:
        if not db.get(Job, job_id):
            raise HTTPException(404, 'Tarefa não encontrada')

    async def events():
        last_id = 0
        while True:
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                rows = list(db.scalars(select(JobEvent).where(JobEvent.job_id == job_id, JobEvent.id > last_id).order_by(JobEvent.id)))
                for item in rows:
                    last_id = item.id
                    payload = {'message': item.message, 'level': item.level,
                               'status': job.status, 'processed': job.processed,
                               'total': job.total, 'added': job.added, 'errors': job.errors}
                    yield f'event: progress\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'
                if job.status in ('done', 'failed'):
                    yield f'event: finished\ndata: {json.dumps({"status": job.status, "added": job.added, "errors": job.errors})}\n\n'
                    break
            yield ': heartbeat\n\n'
            await asyncio.sleep(1)
    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.get('/api/captions', response_model=list[CaptionOut])
def list_captions(_=Depends(auth_session), db: Session = Depends(get_db)):
    return list(db.scalars(select(Caption).order_by(Caption.id.desc())))


@app.post('/api/captions', response_model=CaptionOut, status_code=201)
def create_caption(data: CaptionIn, _=Depends(require_csrf), db: Session = Depends(get_db)):
    if data.category_id is not None:
        get_category(db, data.category_id)
    caption = Caption(**data.model_dump())
    db.add(caption)
    db.commit()
    db.refresh(caption)
    return caption


@app.delete('/api/captions/{caption_id}')
def delete_caption(caption_id: int, _=Depends(require_csrf), db: Session = Depends(get_db)):
    caption = db.get(Caption, caption_id)
    if not caption:
        raise HTTPException(404, 'Legenda não encontrada')
    db.delete(caption)
    db.commit()
    return {'ok': True}


@app.post('/api/captions/generate')
def generate(data: GenerateIn, _=Depends(require_csrf)):
    try:
        return {'texts': generate_captions(data.product, data.audience, data.offer_url, data.tone, data.variants)}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from None


@app.post('/api/posts', status_code=201)
def create_post(data: PostIn, _=Depends(require_csrf), db: Session = Depends(get_db)):
    link = get_link(db, data.link_id)
    post = Post(**data.model_dump())
    link.last_posted_at = utcnow()
    db.add(post)
    db.commit()
    db.refresh(post)
    return {'id': post.id, 'posted_at': post.posted_at.isoformat()}


@app.get('/api/posts')
def list_posts(_=Depends(auth_session), db: Session = Depends(get_db)):
    rows = db.execute(select(Post, Link).join(Link, Post.link_id == Link.id).order_by(Post.id.desc()).limit(100)).all()
    return [{'id': post.id, 'link_id': post.link_id, 'name': link.name, 'platform': link.platform,
             'posted_at': post.posted_at, 'leads': post.leads, 'sales': post.sales, 'notes': post.notes} for post, link in rows]


@app.get('/api/dashboard')
def dashboard(_=Depends(auth_session), db: Session = Depends(get_db)):
    count = lambda stmt: db.scalar(stmt) or 0
    by_platform = dict(db.execute(select(Link.platform, func.count()).group_by(Link.platform)).all())
    by_status = dict(db.execute(select(Link.status, func.count()).group_by(Link.status)).all())
    categories = [{'id': c.id, 'name': c.name, 'emoji': c.emoji,
                   'total': count(select(func.count(Link.id)).where(Link.category_id == c.id))}
                  for c in db.scalars(select(Category).order_by(Category.name))]
    return {'links': count(select(func.count(Link.id))), 'favorites': count(select(func.count(Link.id)).where(Link.favorite.is_(True))),
            'allowed': count(select(func.count(Link.id)).where(Link.permission == 'allowed')),
            'posted': count(select(func.count(Post.id))), 'leads': count(select(func.sum(Post.leads))),
            'sales': count(select(func.sum(Post.sales))), 'by_platform': by_platform,
            'by_status': by_status, 'categories': categories,
            'recent_jobs': [{'id': j.id, 'status': j.status, 'kind': j.kind, 'added': j.added}
                            for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(5))]}


@app.get('/api/export.csv')
def export_csv(_=Depends(auth_session), db: Session = Depends(get_db)):
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['id', 'plataforma', 'nome', 'url', 'categoria', 'status', 'permissao', 'favorito', 'arquivado', 'membros', 'ultima_verificacao', 'ultima_divulgacao', 'observacoes'])
    for link in db.scalars(select(Link).order_by(Link.id)):
        cat = db.get(Category, link.category_id) if link.category_id else None
        # Prefix spreadsheet formula introducers to prevent CSV injection when opened in Excel.
        safe = lambda s: "'" + s if isinstance(s, str) and s.startswith(('=', '+', '-', '@', '\t', '\r')) else s
        writer.writerow([link.id, link.platform, safe(link.name), link.url, safe(cat.name) if cat else '', link.status,
                         link.permission, link.favorite, link.archived, link.members or '', link.last_checked_at or '',
                         link.last_posted_at or '', safe(link.notes)])
    output.seek(0)
    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="radar-grupos.csv"'})


FRONTEND_DIST = Path(os.getenv('FRONTEND_DIST', str(Path(__file__).resolve().parents[2] / 'frontend' / 'dist')))
if FRONTEND_DIST.is_dir():
    app.mount('/assets', StaticFiles(directory=FRONTEND_DIST / 'assets'), name='assets')

    @app.get('/{path:path}', include_in_schema=False)
    def spa(path: str):
        target = (FRONTEND_DIST / path).resolve()
        if not path.startswith('api/') and target.is_relative_to(FRONTEND_DIST.resolve()) and target.is_file():
            return FileResponse(target)
        if path.startswith('api/'):
            raise HTTPException(404, 'Endpoint não encontrado')
        return FileResponse(FRONTEND_DIST / 'index.html')
