#!/usr/bin/env python3
"""Serve loginc.html atrás de uma senha única (sem usuário).

Arquivos no mesmo diretório:
  loginc.html   a página protegida
  senha.hash    "salt_hex:hash_hex" (scrypt) da senha
  segredo.key   chave HMAC dos cookies de sessão (gerada na 1ª execução)
  sensor.token  token que a ESP32 manda no POST /api/sensor (gerado na 1ª execução)

Sensor do PEV:
  POST /api/sensor  Authorization: Bearer <sensor.token>
                    {"pev": "v2", "distancia_cm": 63.2, "altura_cm": 120}
  GET  /api/sensor  (público) última leitura de cada PEV, lida pela página a cada 0,4 s

Só "/" (a demonstração completa) pede senha; /catador, /empresa, /pev, /central
e /prefeitura abrem direto.
"""
import gzip
import hashlib
import hmac
import html
import json
import math
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

BASE = Path(__file__).resolve().parent
PAGINA = BASE / "loginc.html"
PORTA = int(os.environ.get("PORTA", "8770"))
COOKIE = "loginc_sessao"
VALIDADE = 30 * 24 * 3600  # 30 dias
# Rotas isoladas: cada uma abre só um perfil, sem a barra de demonstração.
ROTAS = ("catador", "empresa", "pev", "central", "prefeitura")
ROTULOS = {"catador": "Catador", "empresa": "Empresa", "pev": "PEVs", "central": "Central", "prefeitura": "Prefeitura"}
PEV_ID = re.compile(r"v\d{1,3}")
ALTURA_PADRAO = 120.0  # cm, a mesma altura que a página usa na simulação
LEITURAS: dict = {}  # pev -> última leitura; só em memória, a página ignora o que tiver mais de 20 s
TRAVA = threading.Lock()


def _token_sensor() -> str:
    arq = BASE / "sensor.token"
    if not arq.exists():
        arq.write_text(secrets.token_urlsafe(24) + "\n")
        arq.chmod(0o600)
    return arq.read_text().strip()


def _cm(valor, minimo: float, maximo: float):
    try:
        x = float(valor)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) and minimo <= x <= maximo else None


def _scrypt(senha: str, salt: bytes) -> bytes:
    return hashlib.scrypt(senha.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def _segredo() -> bytes:
    arq = BASE / "segredo.key"
    if not arq.exists():
        arq.write_bytes(secrets.token_bytes(32))
        arq.chmod(0o600)
    return arq.read_bytes()


SALT_HEX, HASH_HEX = (BASE / "senha.hash").read_text().strip().split(":")
SALT, HASH = bytes.fromhex(SALT_HEX), bytes.fromhex(HASH_HEX)
SEGREDO = _segredo()
TOKEN_SENSOR = _token_sensor()


def nome_cookie(escopo: str) -> str:
    return f"{COOKIE}_{escopo}" if escopo else COOKIE


def assinar(escopo: str, expira: int) -> str:
    msg = f"{escopo}|{expira}".encode()
    return f"{expira}.{hmac.new(SEGREDO, msg, hashlib.sha256).hexdigest()}"


def sessao_valida(escopo: str, valor: str) -> bool:
    try:
        expira = int(valor.split(".", 1)[0])
    except ValueError:
        return False
    return expira > time.time() and hmac.compare_digest(valor, assinar(escopo, expira))


_CACHE_PAGINA: dict = {}  # rota -> (mtime, etag, corpo_gzip)


def pagina(rota: str):
    """Página (com a trava da rota, se houver) já comprimida, recalculada quando o arquivo muda."""
    mtime = PAGINA.stat().st_mtime_ns
    item = _CACHE_PAGINA.get(rota)
    if not item or item[0] != mtime:
        corpo = PAGINA.read_bytes()
        if rota:
            corpo = corpo.replace(b"<head>", b"<head>" + trava_rota(rota), 1)
        etag = '"%s"' % hashlib.sha256(corpo).hexdigest()[:20]
        item = (mtime, etag, gzip.compress(corpo, 9), corpo)
        _CACHE_PAGINA[rota] = item
    return item[1:]


def trava_rota(rota: str) -> bytes:
    """Script injetado na rota isolada: mantém a URL no perfil da rota."""
    return (
        "<script>(function(){var v=%r;function f(){if(location.hash!=='#'+v)"
        "history.replaceState(null,'','?isolado#'+v);}f();"
        "addEventListener('hashchange',f);})();</script>" % rota
    ).encode()


FORM = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Loginc</title>
<style>
  :root {{ color-scheme: light dark; --bg:#faf9f5; --fg:#1f1e1b; --card:#fff; --borda:#d9d6cc; --cor:#1F6B46; --erro:#b3261e; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg:#1a1a18; --fg:#ecebe6; --card:#262624; --borda:#3d3c38; --cor:#4fae7f; --erro:#f2b8b5; }} }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:var(--bg); color:var(--fg); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; padding:16px; }}
  form {{ background:var(--card); border:1px solid var(--borda); border-radius:12px; padding:28px; width:100%; max-width:340px; }}
  h1 {{ font-size:20px; margin:0 0 18px; }}
  label {{ display:block; font-size:14px; margin-bottom:6px; }}
  input {{ width:100%; font-size:16px; padding:10px 12px; border:1px solid var(--borda); border-radius:8px; background:transparent; color:inherit; }}
  button {{ margin-top:14px; width:100%; font-size:16px; padding:10px; border:0; border-radius:8px; background:var(--cor); color:#fff; cursor:pointer; }}
  .erro {{ color:var(--erro); font-size:14px; margin:10px 0 0; }}
</style></head>
<body><form method="post" action="/entrar">
  <h1>{titulo}</h1>
  <label for="senha">Senha</label>
  <input id="senha" name="senha" type="password" autocomplete="current-password" autofocus required>
  <input type="hidden" name="volta" id="volta">
  <input type="hidden" name="escopo" value="{escopo}">
  {erro}
  <button type="submit">Entrar</button>
</form>
<script>document.getElementById("volta").value = location.search + location.hash;</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "loginc"
    sys_version = ""

    def _cookie(self, alvo: str) -> str:
        for parte in self.headers.get("Cookie", "").split(";"):
            nome, _, valor = parte.strip().partition("=")
            if nome == alvo:
                return valor
        return ""

    def _enviar(self, status: int, corpo: bytes, tipo="text/html; charset=utf-8", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _pagina(self, rota: str):
        etag, comprimido, cru = pagina(rota)
        # no-cache: o navegador guarda, mas confere o ETag a cada visita (304 se nada mudou)
        cab = [("ETag", etag), ("Vary", "Accept-Encoding")]
        if etag in self.headers.get("If-None-Match", ""):
            self.send_response(304)
            for k, v in cab + [("Cache-Control", "private, no-cache")]:
                self.send_header(k, v)
            self.end_headers()
            return
        gz = "gzip" in self.headers.get("Accept-Encoding", "")
        corpo = comprimido if gz else cru
        if gz:
            cab.append(("Content-Encoding", "gzip"))
        self.send_response(200)
        for k, v in cab + [("Content-Type", "text/html; charset=utf-8"), ("Content-Length", str(len(corpo))),
                           ("Cache-Control", "private, no-cache"), ("X-Frame-Options", "DENY"),
                           ("X-Robots-Tag", "noindex, nofollow"), ("Referrer-Policy", "no-referrer")]:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(corpo)

    def _form(self, escopo: str, status=200, erro=""):
        msg = f'<p class="erro" role="alert">{html.escape(erro)}</p>' if erro else ""
        titulo = "Loginc" + (f" · {ROTULOS[escopo]}" if escopo else "")
        self._enviar(status, FORM.format(erro=msg, escopo=escopo, titulo=titulo).encode())

    def _logado(self, escopo: str) -> bool:
        return sessao_valida(escopo, self._cookie(nome_cookie(escopo)))

    def _json(self, status: int, dados: dict):
        self._enviar(status, json.dumps(dados).encode(), "application/json")

    def do_GET(self):
        caminho, _, query = self.path.partition("?")
        if caminho == "/api/sensor":
            with TRAVA:
                leituras = dict(LEITURAS)
            self._json(200, {"agora": time.time(), "leituras": leituras})
            return
        if caminho == "/sair":  # encerra todas as sessões deste navegador
            extra = [("Location", "/")] + [
                ("Set-Cookie", f"{nome_cookie(e)}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Lax")
                for e in ("",) + ROTAS]
            self._enviar(303, b"", extra=extra)
            return
        rota = caminho.strip("/").lower()
        if rota in ROTAS:
            if "isolado" not in parse_qs(query, keep_blank_values=True):
                self._enviar(302, b"", extra=[("Location", f"/{rota}?isolado#{rota}")])
            else:  # rotas isoladas são públicas, sem senha
                self._pagina(rota)
            return
        if caminho not in ("/", "/index.html"):
            self._enviar(404, b"404")
            return
        if self._logado(""):
            self._pagina("")
        else:
            self._form("")

    do_HEAD = do_GET

    def _sensor(self):
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(token.encode(), TOKEN_SENSOR.encode()):
            self._json(401, {"erro": "token inválido"})
            return
        tamanho = int(self.headers.get("Content-Length", "0") or 0)
        if not 0 < tamanho <= 1024:
            self._json(400, {"erro": "corpo vazio ou grande demais"})
            return
        try:
            dados = json.loads(self.rfile.read(tamanho))
        except ValueError:
            self._json(400, {"erro": "JSON inválido"})
            return
        pev = dados.get("pev") if isinstance(dados, dict) else None
        dist = _cm(dados.get("distancia_cm"), 0, 1000) if pev else None
        altura = _cm(dados.get("altura_cm", ALTURA_PADRAO), 1, 1000) if pev else None
        if not isinstance(pev, str) or not PEV_ID.fullmatch(pev) or dist is None or altura is None:
            self._json(400, {"erro": "esperado {pev: 'v2', distancia_cm: 0..1000, altura_cm: 1..1000}"})
            return
        nivel = round(min(1.0, max(0.0, (altura - dist) / altura)), 3)
        with TRAVA:
            LEITURAS[pev] = {"cm": round(dist, 1), "altura": round(altura, 1), "nivel": nivel, "t": time.time()}
        self._json(200, {"ok": True, "pev": pev, "nivel": nivel})

    def do_POST(self):
        if self.path.split("?", 1)[0] == "/api/sensor":
            self._sensor()
            return
        if self.path.split("?", 1)[0] != "/entrar":
            self._enviar(404, b"404")
            return
        tamanho = min(int(self.headers.get("Content-Length", "0") or 0), 4096)
        dados = parse_qs(self.rfile.read(tamanho).decode(errors="replace"))
        senha = dados.get("senha", [""])[0]
        escopo = dados.get("escopo", [""])[0]
        if escopo not in ROTAS:
            escopo = ""
        volta = dados.get("volta", [""])[0]
        if not volta.startswith(("?", "#")) or any(c in volta for c in "\r\n/\\"):
            volta = ""  # só aceita ?query#hash da própria página
        destino = f"/{escopo}?isolado#{escopo}" if escopo else "/" + volta
        if hmac.compare_digest(_scrypt(senha, SALT), HASH):
            expira = int(time.time()) + VALIDADE
            self._enviar(303, b"", extra=[
                ("Location", destino),
                ("Set-Cookie", f"{nome_cookie(escopo)}={assinar(escopo, expira)}; Path=/; "
                               f"Max-Age={VALIDADE}; HttpOnly; Secure; SameSite=Lax"),
            ])
        else:
            time.sleep(1.5)  # freia tentativas em série
            self._form(escopo, 401, "Senha incorreta.")

    def log_message(self, fmt, *args):
        ip = self.headers.get("X-Forwarded-For", self.client_address[0]) if self.headers else self.client_address[0]
        print(f"{self.log_date_time_string()} {ip} {fmt % args}", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORTA), Handler).serve_forever()
