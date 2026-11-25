"""QR code do Loginc nas cores do slide, com o selo (círculo amarelo + reciclagem) no centro.

Gera, na pasta passada como argumento:
  loginc-qrcode.svg / .png          cartão verde com o QR, "loginc." e o endereço
  loginc-qrcode-simples.svg / .png  só o QR (fundo creme), para colar em outros materiais
"""
import sys
from pathlib import Path

import segno
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

URL = "https://loginc.tailb54547.ts.net/"
VERDE, CREME, ESCURO, AMARELO = "#1F6B46", "#F4F0E6", "#15281D", "#F2B33D"
FONTE = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONTE_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SAIDA = Path(sys.argv[1])
SAIDA.mkdir(parents=True, exist_ok=True)

qr = segno.make(URL, error="h", boost_error=False)
M = [[bool(c) for c in linha] for linha in qr.matrix]
N = len(M)
QUIET = 2  # margem em módulos dentro do painel creme
LOGO_MOD = 9.6  # diâmetro do selo em módulos (~6,6% da área; correção H aguenta 30%)
c0 = N / 2


def eh_finder(r, c):
    return any(r0 <= r < r0 + 7 and c0_ <= c < c0_ + 7 for r0, c0_ in ((0, 0), (0, N - 7), (N - 7, 0)))


def sob_logo(r, c):
    folga = LOGO_MOD / 2 + 0.6  # anel creme entre o selo e os módulos
    return (r + 0.5 - c0) ** 2 + (c + 0.5 - c0) ** 2 < folga ** 2


def glifo_reciclagem_svg(cx, cy, tamanho):
    """Caminho SVG do ♻ (U+267B) da DejaVu, centrado em (cx, cy) com altura ~tamanho."""
    fonte = TTFont(FONTE)
    nome = fonte.getBestCmap()[0x267B]
    gs = fonte.getGlyphSet()
    from fontTools.pens.boundsPen import BoundsPen
    bp = BoundsPen(gs)
    gs[nome].draw(bp)
    x0, y0, x1, y1 = bp.bounds
    esc = tamanho / max(x1 - x0, y1 - y0)
    pen = SVGPathPen(gs)
    # inverte o eixo y (fonte cresce para cima) e centra
    t = (esc, 0, 0, -esc, cx - (x0 + x1) / 2 * esc, cy + (y0 + y1) / 2 * esc)
    gs[nome].draw(TransformPen(pen, t))
    return pen.getCommands()


def svg_qr(ox, oy, mod):
    """Elementos SVG do painel creme + módulos + selo, com o canto superior esquerdo em (ox, oy)."""
    lado = (N + 2 * QUIET) * mod
    p = [f'<rect x="{ox}" y="{oy}" width="{lado}" height="{lado}" rx="{mod * 2}" fill="{CREME}"/>']
    bx, by = ox + QUIET * mod, oy + QUIET * mod
    d = []
    for r in range(N):
        for c in range(N):
            if M[r][c] and not eh_finder(r, c) and not sob_logo(r, c):
                d.append(f"M{bx + c * mod:.2f} {by + r * mod:.2f}h{mod:.2f}v{mod:.2f}h{-mod:.2f}z")
    p.append(f'<path fill="{VERDE}" d="{"".join(d)}"/>')
    for r0, c0_ in ((0, 0), (0, N - 7), (N - 7, 0)):
        x, y = bx + c0_ * mod, by + r0 * mod
        p.append(f'<rect x="{x + mod / 2}" y="{y + mod / 2}" width="{6 * mod}" height="{6 * mod}" rx="0" '
                 f'fill="none" stroke="{VERDE}" stroke-width="{mod}"/>')
        p.append(f'<rect x="{x + 2 * mod}" y="{y + 2 * mod}" width="{3 * mod}" height="{3 * mod}" fill="{ESCURO}"/>')
    cx, cy = bx + c0 * mod, by + c0 * mod
    raio = LOGO_MOD / 2 * mod
    p.append(f'<circle cx="{cx}" cy="{cy}" r="{raio}" fill="{AMARELO}"/>')
    p.append(f'<path fill="#111111" d="{glifo_reciclagem_svg(cx, cy, raio * 1.4)}"/>')
    return p, lado


def png_qr(img, ox, oy, mod):
    dr = ImageDraw.Draw(img)
    lado = (N + 2 * QUIET) * mod
    dr.rounded_rectangle([ox, oy, ox + lado - 1, oy + lado - 1], radius=mod * 2, fill=CREME)
    bx, by = ox + QUIET * mod, oy + QUIET * mod
    for r in range(N):
        for c in range(N):
            if M[r][c] and not eh_finder(r, c) and not sob_logo(r, c):
                dr.rectangle([bx + c * mod, by + r * mod, bx + (c + 1) * mod - 1, by + (r + 1) * mod - 1], fill=VERDE)
    for r0, c0_ in ((0, 0), (0, N - 7), (N - 7, 0)):
        x, y = bx + c0_ * mod, by + r0 * mod
        dr.rounded_rectangle([x, y, x + 7 * mod - 1, y + 7 * mod - 1], radius=0, fill=VERDE)
        dr.rounded_rectangle([x + mod, y + mod, x + 6 * mod - 1, y + 6 * mod - 1], radius=0, fill=CREME)
        dr.rounded_rectangle([x + 2 * mod, y + 2 * mod, x + 5 * mod - 1, y + 5 * mod - 1], radius=0, fill=ESCURO)
    cx, cy = bx + c0 * mod, by + c0 * mod
    raio = LOGO_MOD / 2 * mod
    dr.ellipse([cx - raio, cy - raio, cx + raio, cy + raio], fill=AMARELO)
    f = ImageFont.truetype(FONTE, int(raio * 1.85))
    dr.text((cx, cy), "♻", font=f, fill="#111111", anchor="mm")
    return lado


def salvar_simples(mod_png=48, mod_svg=10):
    p, lado = svg_qr(0, 0, mod_svg)
    (SAIDA / "loginc-qrcode-simples.svg").write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {lado} {lado}" width="{lado}" height="{lado}">'
        + "".join(p) + "</svg>\n")
    lado_png = (N + 2 * QUIET) * mod_png
    img = Image.new("RGBA", (lado_png, lado_png), (0, 0, 0, 0))
    png_qr(img, 0, 0, mod_png)
    img.save(SAIDA / "loginc-qrcode-simples.png")


def salvar_cartao(mod_png=40, mod_svg=10):
    for fmt, mod in (("svg", mod_svg), ("png", mod_png)):
        lado_qr = (N + 2 * QUIET) * mod
        pad = 5 * mod
        larg = lado_qr + 2 * pad
        alt_marca = 9 * mod
        alt = pad + alt_marca + lado_qr + 6 * mod + pad
        qy = pad + alt_marca
        tam_marca = 5.4 * mod
        if fmt == "svg":
            p, _ = svg_qr(pad, qy, mod)
            marca = (f'<text x="{larg / 2}" y="{pad + 6 * mod}" text-anchor="middle" '
                     f'font-family="Inter, Helvetica, Arial, sans-serif" font-weight="700" '
                     f'font-size="{tam_marca}" letter-spacing="{-0.04 * tam_marca}" fill="{CREME}">'
                     f'loginc<tspan fill="{AMARELO}">.</tspan></text>')
            rodape = (f'<rect x="{pad}" y="{qy + lado_qr + 1.6 * mod}" width="{lado_qr}" height="{3.4 * mod}" fill="{ESCURO}"/>'
                      f'<text x="{larg / 2}" y="{qy + lado_qr + 3.85 * mod}" text-anchor="middle" '
                      f'font-family="Inter, Helvetica, Arial, sans-serif" font-size="{1.5 * mod}" fill="{CREME}">'
                      f'Coleta que vira renda</text>')
            (SAIDA / "loginc-qrcode.svg").write_text(
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {larg} {alt}" width="{larg}" height="{alt}">'
                f'<rect width="{larg}" height="{alt}" rx="{2 * mod}" fill="{VERDE}"/>'
                + marca + "".join(p) + rodape + "</svg>\n")
        else:
            img = Image.new("RGB", (int(larg), int(alt)), VERDE)
            png_qr(img, pad, qy, mod)
            dr = ImageDraw.Draw(img)
            fb = ImageFont.truetype(FONTE_BOLD, int(tam_marca))
            w_l = dr.textlength("loginc", font=fb)
            w_p = dr.textlength(".", font=fb)
            x = (larg - w_l - w_p) / 2
            base = pad + 6 * mod
            dr.text((x, base), "loginc", font=fb, fill=CREME, anchor="ls")
            dr.text((x + w_l, base), ".", font=fb, fill=AMARELO, anchor="ls")
            y0 = qy + lado_qr + 1.6 * mod
            dr.rectangle([pad, y0, pad + lado_qr - 1, y0 + 3.4 * mod], fill=ESCURO)
            fr = ImageFont.truetype(FONTE, int(1.5 * mod))
            dr.text((larg / 2, y0 + 1.7 * mod), "Coleta que vira renda", font=fr, fill=CREME, anchor="mm")
            img.save(SAIDA / "loginc-qrcode.png")


salvar_simples()
salvar_cartao()
print("versão", qr.version, "módulos", N, "correção", qr.error)
