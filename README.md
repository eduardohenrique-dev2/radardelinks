# Radar de Grupos

Plataforma pessoal para **descobrir, organizar e acompanhar links públicos de Telegram, WhatsApp e Facebook**. Login privado; categorias, busca Brave com tarefas em segundo plano e SSE, deduplicação, validação proporcional, favoritos, filtros, histórico, modelos de divulgação com IA Anthropic, CSV, tema claro/escuro.

**Limites importantes:** o aplicativo **nunca entra em grupos nem publica mensagens**. Não consulta membros de WhatsApp/Facebook nem acessa essas plataformas para raspagem. Telegram usa apenas prévia pública do `t.me`, que pode falhar. Links de outras plataformas e convites privados exigem verificação manual. Acesso a um link não comprova permissão de divulgação.

## Configurar

```bash
cp .env.example .env
python -c "import secrets;print(secrets.token_urlsafe(48))"
```

Edite `.env`: defina `ADMIN_USERNAME`, `ADMIN_PASSWORD` forte (16+ caracteres), `SECRET_KEY` gerada acima e `POSTGRES_PASSWORD`. Para buscas, configure `BRAVE_API_KEY` ([Brave](https://brave.com/search/api/)); para gerar legendas por IA, `ANTHROPIC_API_KEY` ([Anthropic](https://console.anthropic.com/)). **Não** comite o `.env`.

## Local sem Docker (modo desenvolvimento)

Pré-requisitos: Python 3.12+ e Node 22+. Deixe `DATABASE_URL=sqlite:///./radar.db` em `.env` (SQLite apenas para desenvolvimento). Do diretório raiz:

```bash
python -m venv .venv
source .venv/bin/activate             # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
cd backend && uvicorn app.main:app --reload --port 8000
```

Em outro terminal:

```bash
cd frontend
npm install
npm run dev
```

Abra **http://localhost:5173**. Busca agendada e progresso SSE rodam no processo FastAPI. **Não use múltiplos workers uvicorn.** Sem chaves de API, a importação manual, organização, filtros, legendas salvas e exportação funcionam; buscas Brave e geração de IA exibem erro de configuração.

## Docker Compose (PostgreSQL)

```bash
docker compose up --build -d
docker compose logs -f app
```

Abra **http://localhost:8000**. O Compose usa PostgreSQL 17 com volume persistente e acessa a aplicação somente via `127.0.0.1:8000`. Para parar: `docker compose down` (não use `-v`, que apaga o banco).

## VPS e HTTPS

1. Ubuntu 24.04, Docker/Compose e DNS `radar.seudominio.com` apontando à VPS. Clone o repositório e configure o `.env`.
2. No `.env`, use `SECURE_COOKIES=true` e `ALLOWED_ORIGIN=https://radar.seudominio.com`.
3. Inicie `docker compose up -d --build`. Deixe a aplicação ligada apenas ao loopback.
4. Instale [Caddy](https://caddyserver.com/docs/install) na VPS e crie `/etc/caddy/Caddyfile`:

```caddyfile
radar.seudominio.com {
    reverse_proxy 127.0.0.1:8000
}
```

5. Abra TCP 80/443 no firewall, execute `sudo systemctl reload caddy`. Caddy providencia HTTPS/Let's Encrypt automaticamente. Faça backup do volume PostgreSQL (`pg_dump`) e teste restauração.

**Custo estimado:** VPS pequena 1–2 GB: cerca de US$5–12/mês (varia por provedor); Brave: US$5/1.000 consultas com créditos sujeitos a condições; Anthropic: consumo de tokens; domínio em separado. Exemplo de MVP: **US$5–25/mês**, dependendo de uso e região. Não há implantação automática em VPS.

## Testes

```bash
PYTHONPATH=backend pytest -q backend/tests
cd frontend && npm install && npm run build
```

Os testes simulam Brave/Anthropic e a prévia do Telegram. Para teste integrado com APIs reais, forneça suas chaves no `.env` e execute as ações pela interface. Endpoints em `/docs`; saúde em `/api/health`.

## Segurança

Sessão assinada expira em 7 dias, cookie `httpOnly`/`SameSite=Strict`, token CSRF, limitação de tentativas de login, validação de URLs por allowlist de domínio/caminho, sem redirecionamentos externos em checagens. Use HTTPS e `SECURE_COOKIES=true` em produção. A única conta é definida via variáveis de ambiente. Em versões futuras: migrações Alembic, backups automáticos monitorados, métricas de conversão por campanha, mais filtros e revisão de privilégios em escala.
