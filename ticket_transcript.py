import html
import io
from datetime import timezone


def esc(value):
    return html.escape(str(value or ""), quote=True)


def nl(value):
    return esc(value).replace("\n", "<br>")


async def build_ticket_transcript(channel, closed_by, reason):
    messages = [message async for message in channel.history(limit=None, oldest_first=True)]
    rows = []
    for message in messages:
        author = message.author
        avatar = str(author.display_avatar.with_size(64).url) if getattr(author, "display_avatar", None) else ""
        timestamp = message.created_at.astimezone(timezone.utc).strftime("%d.%m.%Y %H:%M:%S UTC")
        body = []
        if message.content:
            body.append(f'<div class="content">{nl(message.clean_content)}</div>')
        for attachment in message.attachments:
            url = esc(attachment.url)
            filename = esc(attachment.filename)
            if str(attachment.content_type or "").startswith("image/"):
                body.append(f'<a class="attachment" href="{url}">📎 {filename}</a><br><img class="image" src="{url}" alt="{filename}">')
            else:
                body.append(f'<a class="attachment" href="{url}">📎 {filename}</a>')
        for embed in message.embeds:
            parts = []
            if embed.title:
                parts.append(f'<div class="embed-title">{esc(embed.title)}</div>')
            if embed.description:
                parts.append(f'<div>{nl(embed.description)}</div>')
            for field in embed.fields:
                parts.append(f'<div class="field"><b>{esc(field.name)}</b><br>{nl(field.value)}</div>')
            if parts:
                body.append('<div class="embed">' + ''.join(parts) + '</div>')
        if not body:
            body.append('<div class="muted">Системное сообщение без текста</div>')
        rows.append(f'''<article class="message">
<img class="avatar" src="{esc(avatar)}" alt="">
<div class="message-body"><div><span class="author">{esc(getattr(author, 'display_name', author))}</span><span class="id">ID {author.id}</span><time>{timestamp}</time></div>{''.join(body)}</div>
</article>''')
    owner_id = "не определён"
    topic = channel.topic or ""
    if topic.startswith("ticket-owner:"):
        owner_id = topic.split(":", 2)[1]
    document = f'''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Тикет {esc(channel.name)}</title>
<style>
:root{{--bg:#07030d;--panel:#140a22;--card:#1d1030;--line:#8238d8;--accent:#b83cff;--text:#f8f4ff;--muted:#bba8ca}}
*{{box-sizing:border-box}}body{{margin:0;background:linear-gradient(135deg,#05020a,#160623);color:var(--text);font-family:Arial,sans-serif}}
main{{max-width:1050px;margin:32px auto;padding:0 18px}}header{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:24px;margin-bottom:20px}}
h1{{margin:0 0 12px;color:var(--accent)}}.meta{{color:var(--muted);line-height:1.65}}.message{{display:flex;gap:14px;background:rgba(20,10,34,.94);border-left:3px solid var(--line);padding:16px;margin:10px 0;border-radius:12px}}
.avatar{{width:46px;height:46px;border-radius:50%;background:#3c2450;object-fit:cover}}.message-body{{min-width:0;flex:1}}.author{{font-weight:700;font-size:17px}}.id,time{{color:var(--muted);font-size:12px;margin-left:10px}}.content{{margin-top:8px;white-space:normal;overflow-wrap:anywhere}}
.embed{{margin-top:10px;padding:12px;border-left:4px solid var(--accent);background:var(--card);border-radius:7px}}.embed-title{{font-weight:700;margin-bottom:6px}}.field{{margin-top:9px}}.attachment{{display:inline-block;color:#d98cff;margin-top:9px;text-decoration:none}}.image{{display:block;max-width:720px;max-height:520px;border-radius:10px;margin-top:8px}}.muted{{color:var(--muted);margin-top:7px}}
</style></head><body><main><header><h1>Журнал тикета: #{esc(channel.name)}</h1><div class="meta">Сервер: {esc(channel.guild.name)}<br>Автор тикета: {esc(owner_id)}<br>Закрыл: {esc(closed_by)} (ID {closed_by.id})<br>Причина: {esc(reason)}<br>Сообщений: {len(messages)}</div></header>{''.join(rows) if rows else '<div class="message">В тикете не было сообщений.</div>'}</main></body></html>'''
    stream = io.BytesIO(document.encode("utf-8"))
    stream.seek(0)
    return stream, len(messages)
