import io
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from elo_levels import elo_level

ROOT=Path(__file__).resolve().parent
W,H=1200,1500
PURPLE=(132,74,255); VIOLET=(197,139,255); CYAN=(78,220,255); WHITE=(247,245,255); MUTED=(167,159,192)
LEAGUE_COLORS={'Default':(190,194,210),'Qualifications':(55,218,145),'Division':(170,82,255),'Pro':(255,70,101)}


def font(size,bold=False):
    path=Path('/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf')
    return ImageFont.truetype(str(path),size) if path.exists() else ImageFont.load_default()


def avatar(url,size):
    try:
        r=requests.get(str(url),timeout=8); r.raise_for_status()
        return ImageOps.fit(Image.open(io.BytesIO(r.content)).convert('RGB'),(size,size),Image.Resampling.LANCZOS)
    except Exception: return Image.new('RGB',(size,size),(30,24,55))


def round_paste(canvas,image,xy,size):
    mask=Image.new('L',(size,size)); ImageDraw.Draw(mask).ellipse((0,0,size,size),fill=255); canvas.paste(image,xy,mask)


def build_leaderboard(rows,league):
    accent=LEAGUE_COLORS.get(league,PURPLE)
    canvas=Image.new('RGBA',(W,H),(5,4,14,255)); d=ImageDraw.Draw(canvas,'RGBA')
    glow=Image.new('RGBA',(W,H),(0,0,0,0)); gd=ImageDraw.Draw(glow)
    gd.ellipse((-300,-250,850,700),fill=(*PURPLE,100)); gd.ellipse((650,850,1500,1700),fill=(*CYAN,35))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(150))); d=ImageDraw.Draw(canvas,'RGBA')
    for x in range(-180,1400,190): d.polygon([(x,0),(x+78,0),(x-90,270),(x-160,270)],fill=(*VIOLET,26))
    logo_path=ROOT/'assets'/'dominion-logo.png'
    if logo_path.exists(): canvas.alpha_composite(ImageOps.fit(Image.open(logo_path).convert('RGBA'),(124,124)),(46,38))
    d.text((190,52),'DOMINION FACEIT',font=font(38,True),fill=WHITE)
    d.text((190,103),'GLOBAL COMPETITIVE RANKING',font=font(17,True),fill=VIOLET)
    d.rounded_rectangle((42,190,1158,335),radius=34,fill=(17,14,35,238),outline=(*accent,210),width=3)
    d.text((78,222),f'{league.upper()} LEAGUE',font=font(26,True),fill=(*accent,255))
    d.text((78,264),'TOP 10 PLAYERS',font=font(46,True),fill=WHITE)
    d.text((1118,265),'ELO • LEVEL • MATCH RECORD',font=font(17,True),anchor='ra',fill=MUTED)
    medals=[(255,214,92),(205,212,225),(201,133,78)]
    y=380
    if not rows:
        d.text((600,730),'В этой лиге пока нет игроков',font=font(29,True),anchor='mm',fill=MUTED)
    for index,item in enumerate(rows[:10],1):
        height=78 if index>3 else 92
        fill=(25,20,49,244) if index<=3 else (16,14,31,235)
        outline=medals[index-1] if index<=3 else accent
        d.rounded_rectangle((42,y,1158,y+height),radius=24,fill=fill,outline=(*outline,220),width=3 if index<=3 else 1)
        size=62 if index<=3 else 50; av=avatar(item.get('avatar_url'),size); round_paste(canvas,av,(82,y+(height-size)//2),size)
        d.ellipse((78,y+(height-size)//2-4,86+size,y+(height-size)//2+size+4),outline=(*outline,255),width=4)
        d.text((50,y+height//2),f'#{index}',font=font(23,True),anchor='lm',fill=(*outline,255))
        d.text((185,y+height//2-18),item['name'][:24],font=font(25,True),anchor='lm',fill=WHITE)
        d.text((185,y+height//2+20),f"{item.get('wins',0)}W • {item.get('games',0)} MATCHES",font=font(15,True),anchor='lm',fill=MUTED)
        d.text((890,y+height//2-15),f"LVL {elo_level(item['points'])}",font=font(22,True),anchor='rm',fill=(*accent,255))
        d.text((1115,y+height//2+15),f"{item['points']} ELO",font=font(29,True),anchor='rm',fill=WHITE)
        y+=height+12
    d.text((600,1450),'DOMINION FACEIT  •  RISE ABOVE',font=font(17,True),anchor='mm',fill=(*accent,230))
    out=io.BytesIO(); canvas.convert('RGB').save(out,'PNG',quality=96); out.seek(0); return out
