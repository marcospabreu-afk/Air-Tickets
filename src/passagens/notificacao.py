"""Envio dos alertas por e-mail.

Dois jeitos, escolhidos pelas variáveis de ambiente (nenhuma senha fica no código):

* **Resend** (HTTPS) — use no Render grátis, que bloqueia as portas de SMTP:
    RESEND_API_KEY       chave criada em https://resend.com/api-keys
    PASSAGENS_EMAIL_PARA endereço que recebe os alertas
    PASSAGENS_EMAIL_DE   remetente (padrão: "Passagens <onboarding@resend.dev>", que só
                         entrega para o e-mail da sua conta Resend; para outros
                         destinatários, verifique um domínio no Resend)

* **SMTP** (ex.: Gmail com senha de app) — no seu computador:
    PASSAGENS_EMAIL_PARA, SMTP_USUARIO, SMTP_SENHA
    SMTP_HOST (padrão smtp.gmail.com), SMTP_PORTA (587), SMTP_SEGURANCA (starttls|ssl|nenhuma)
"""

from __future__ import annotations

import html
import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Callable, Sequence

from .alertas import formatar_brl
from .modelos import Alerta

URL_RESEND = "https://api.resend.com/emails"
REMETENTE_RESEND_PADRAO = "Passagens <onboarding@resend.dev>"


class ErroDeEnvio(RuntimeError):
    """Falha ao enviar o e-mail."""


@dataclass(frozen=True)
class Mensagem:
    assunto: str
    texto: str
    html: str


def montar_mensagem(alertas: Sequence[Alerta], url_site: str = "") -> Mensagem:
    menor = min(alertas, key=lambda a: a.preco)
    if len(alertas) == 1:
        assunto = f"Passagem baixou: {menor.rota_rotulo.split(' (')[0]} por {formatar_brl(menor.preco)}"
    else:
        assunto = f"{len(alertas)} alertas de passagem — a partir de {formatar_brl(menor.preco)}"
    linhas_txt, linhas_html = [], []
    for a in alertas:
        datas = f"{a.data_ida:%d/%m/%Y} → {a.data_volta:%d/%m/%Y}"
        linhas_txt.append(f"• {a.rota_rotulo}\n  {formatar_brl(a.preco)} · {a.companhia} · chegada {a.destino}"
                          f" · {datas}\n  Motivo: {a.motivos}\n  {a.link}")
        linhas_html.append(
            f"<li><b>{html.escape(a.rota_rotulo)}</b><br>"
            f"<span style='font-size:18px'><b>{formatar_brl(a.preco)}</b></span> · {html.escape(a.companhia)}"
            f" · chegada {html.escape(a.destino)} · {datas}<br>"
            f"<small>{html.escape(a.motivos)}</small><br>"
            f"<a href='{html.escape(a.link, quote=True)}'>Ver no Google Flights</a></li>")
    rodape = f"\n\nRotas e alertas: {url_site.rstrip('/')}/monitorar" if url_site else ""
    texto = "O preço caiu nas rotas que você monitora:\n\n" + "\n\n".join(linhas_txt) + rodape
    corpo = ("<p>O preço caiu nas rotas que você monitora:</p><ul>" + "".join(linhas_html) + "</ul>"
             + (f"<p><a href='{html.escape(url_site.rstrip('/'))}/monitorar'>Abrir o monitoramento</a></p>"
                if url_site else ""))
    return Mensagem(assunto, texto, corpo)


class Notificador:
    """Envia e-mails conforme as variáveis de ambiente (ou valores passados nos testes)."""

    def __init__(self, ambiente: dict | None = None, http_post: Callable | None = None,
                 smtp_classe: Callable | None = None):
        env = os.environ if ambiente is None else ambiente
        self.para = env.get("PASSAGENS_EMAIL_PARA", "").strip()
        self.resend_chave = env.get("RESEND_API_KEY", "").strip()
        self.remetente = env.get("PASSAGENS_EMAIL_DE", "").strip()
        self.smtp_usuario = env.get("SMTP_USUARIO", "").strip()
        self.smtp_senha = env.get("SMTP_SENHA", "")
        self.smtp_host = env.get("SMTP_HOST", "smtp.gmail.com").strip()
        self.smtp_porta = int(env.get("SMTP_PORTA", "587"))
        self.smtp_seguranca = env.get("SMTP_SEGURANCA", "starttls").strip().lower()
        self._post = http_post
        self._smtp = smtp_classe

    @property
    def metodo(self) -> str | None:
        if not self.para:
            return None
        if self.resend_chave:
            return "resend"
        if self.smtp_usuario and self.smtp_senha:
            return "smtp"
        return None

    @property
    def configurado(self) -> bool:
        return self.metodo is not None

    def descricao(self) -> str:
        if self.metodo == "resend":
            return f"e-mail via Resend para {self.para}"
        if self.metodo == "smtp":
            return f"e-mail via SMTP ({self.smtp_host}) para {self.para}"
        return "e-mail não configurado"

    def enviar(self, mensagem: Mensagem) -> None:
        if self.metodo == "resend":
            self._enviar_resend(mensagem)
        elif self.metodo == "smtp":
            self._enviar_smtp(mensagem)
        else:
            raise ErroDeEnvio("e-mail não configurado (veja PASSAGENS_EMAIL_PARA no README)")

    def _enviar_resend(self, m: Mensagem) -> None:
        post = self._post
        if post is None:
            import requests

            post = requests.post
        resposta = post(URL_RESEND, headers={"Authorization": f"Bearer {self.resend_chave}"},
                        json={"from": self.remetente or REMETENTE_RESEND_PADRAO, "to": [self.para],
                              "subject": m.assunto, "text": m.texto, "html": m.html}, timeout=30)
        if resposta.status_code >= 300:
            try:
                detalhe = resposta.json().get("message", "")
            except ValueError:
                detalhe = ""
            raise ErroDeEnvio(f"Resend respondeu HTTP {resposta.status_code} {detalhe}".strip())

    def _enviar_smtp(self, m: Mensagem) -> None:
        msg = EmailMessage()
        msg["Subject"] = m.assunto
        msg["From"] = self.remetente or self.smtp_usuario
        msg["To"] = self.para
        msg.set_content(m.texto)
        msg.add_alternative(m.html, subtype="html")
        classe = self._smtp or (smtplib.SMTP_SSL if self.smtp_seguranca == "ssl" else smtplib.SMTP)
        try:
            with classe(self.smtp_host, self.smtp_porta, timeout=30) as servidor:
                if self.smtp_seguranca == "starttls":
                    servidor.starttls()
                if self.smtp_seguranca != "nenhuma":  # "nenhuma": servidor local de testes
                    servidor.login(self.smtp_usuario, self.smtp_senha)
                servidor.send_message(msg)
        except (OSError, smtplib.SMTPException) as exc:
            raise ErroDeEnvio(f"SMTP {self.smtp_host}:{self.smtp_porta}: {exc}") from exc
