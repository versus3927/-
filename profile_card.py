import asyncio
import io
import math
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from elo_levels import elo_bounds, elo_level

ROOT=Path(__file__).resolve().parent
W,H=1800,1400
BG=(3,3,10); PANEL=(13,12,24); PANEL_2=(22,20,36)
PURPLE=(126,70,238); VIOLET=(181,119,255); CYAN=(76,200,232)
WHITE=(244,242,250); MUTED=(159,153,178); GREEN=(62,210,137); RED=(240,74,116); GOLD=(244,190,72)
LEAGUE_COLORS={"Default":(184,188,205),"Qualifications":(54,211,139),"Division":(164,83,246),"Pro":(244,68,96)}
MAP_NAMES=("Sandstone","Province","Rust","Dune","Hanami","Breeze","Prison")


def font(size,bold=False):
    local=ROOT/'assets'/'fonts'/('DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf')
    system=Path('/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf')
    path=local if local.exists() else system
    return ImageFont.truetype(str(path),size) if path.exists() else ImageFont.load_default()


def text(draw,xy,value,size,color=WHITE,bold=False,anchor=None):
    draw.text(xy,str(value),font=font(size,bold),fill=(*color,255),anchor=anchor)


def panel(draw,box,radius=28,fill=PANEL,outline=(58,46,89),width=2):
    x1,y1,x2,y2=box
    draw.rounded_rectangle((x1+8,y1+10,x2+8,y2+10),radius=radius,fill=(0,0,0,100))
    draw.rounded_rectangle(box,radius=radius,fill=(*fill,242),outline=(*outline,205),width=width)


def line_progress(draw,box,ratio,color=PURPLE):
    x1,y1,x2,y2=box; ratio=max(0.0,min(1.0,float(ratio)))
    draw.rounded_rectangle(box,radius=9,fill=(48,44,65,255))
    if ratio>0:
        end=max(x1+14,int(x1+(x2-x1)*ratio))
        draw.rounded_rectangle((x1,y1,end,y2),radius=9,fill=(*color,255))
        draw.ellipse((end-8,y1-4,end+8,y2+4),fill=(*CYAN,255))


def fetch_avatar(url,size):
    try:
        response=requests.get(str(url),timeout=10); response.raise_for_status()
        image=Image.open(io.BytesIO(response.content)).convert('RGB')
        return ImageOps.fit(image,(size,size),Image.Resampling.LANCZOS)
    except Exception:
        image=Image.new('RGB',(size,size),(10,9,20)); d=ImageDraw.Draw(image)
        d.ellipse((size*.27,size*.15,size*.73,size*.60),fill=(70,63,91))
        d.rounded_rectangle((size*.17,size*.58,size*.83,size*.95),radius=int(size*.2),fill=(70,63,91))
        return image


def paste_round(canvas,image,box,radius):
    x1,y1,x2,y2=box
    image=ImageOps.fit(image,(x2-x1,y2-y1),Image.Resampling.LANCZOS)
    mask=Image.new('L',image.size); ImageDraw.Draw(mask).rounded_rectangle((0,0,image.width-1,image.height-1),radius=radius,fill=255)
    canvas.paste(image,(x1,y1),mask)


def league_for(points):
    if points>=1600: return "Pro"
    if points>=1350: return "Division"
    if points>=1150: return "Qualifications"
    return "Default"


def match_result(match,user_id):
    if match.get('score_a') is None or match.get('score_b') is None: return None
    team_a=str(user_id) in str(match.get('team_a','')).split(',')
    won_a=int(match['score_a'])>int(match['score_b'])
    return 'W' if team_a==won_a else 'L'


def map_records(recent,user_id):
    rows={name:[0,0] for name in MAP_NAMES}
    for match in recent:
        name=str(match.get('map') or '')
        result=match_result(match,user_id)
        if name in rows and result:
            rows[name][0 if result=='W' else 1]+=1
    used=[(name,*record) for name,record in rows.items() if sum(record)>0]
    empty=[(name,0,0) for name in MAP_NAMES if sum(rows[name])==0]
    return (used+empty)[:6]


def draw_brand_mark(draw,x,y,size=72):
    # Original vector crown/shield mark; no supplied reference image is embedded.
    c=VIOLET
    draw.polygon([(x,y+20),(x+18,y+39),(x+36,y+6),(x+54,y+39),(x+72,y+20),(x+63,y+55),(x+9,y+55)],fill=(*c,235),outline=(*WHITE,170))
    draw.polygon([(x+14,y+64),(x+58,y+64),(x+52,y+104),(x+36,y+120),(x+20,y+104)],outline=(*CYAN,220),fill=(*PURPLE,85),width=4)
    text(draw,(x+36,y+89),'D',28,WHITE,True,'mm')


def draw_level_badge(draw,cx,cy,level,accent):
    for radius,color,alpha,width in [(82,PURPLE,55,2),(69,accent,225,3),(56,(8,7,18),255,2)]:
        pts=[(cx+radius*math.cos(math.radians(60*i-30)),cy+radius*math.sin(math.radians(60*i-30))) for i in range(6)]
        draw.polygon(pts,fill=(*color,alpha),outline=(*CYAN,180),width=width)
    text(draw,(cx,cy-8),level,50,WHITE,True,'mm'); text(draw,(cx,cy+36),'LEVEL',14,MUTED,True,'mm')


def draw_donut(draw,center,radius,ratio,primary,secondary,label,value):
    cx,cy=center; box=(cx-radius,cy-radius,cx+radius,cy+radius)
    draw.arc(box,-90,270,fill=(55,51,73),width=18)
    draw.arc(box,-90,-90+360*max(0,min(1,ratio)),fill=primary,width=18)
    if ratio<1: draw.arc(box,-90+360*ratio,270,fill=secondary,width=18)
    text(draw,(cx,cy-8),value,38,WHITE,True,'mm'); text(draw,(cx,cy+31),label,14,MUTED,True,'mm')


def build_profile_card_sync(player,display_name,avatar_url,recent):
    points=max(0,int(player.get('points',0))); level=elo_level(points); league=league_for(points); accent=LEAGUE_COLORS[league]
    games=int(player.get('games',0)); wins=int(player.get('wins',0)); losses=int(player.get('losses',max(0,games-wins)))
    kills=int(player.get('kills',0)); deaths=int(player.get('deaths',0)); assists=int(player.get('assists',0)); mvp=int(player.get('mvp',0))
    kd=kills/max(1,deaths); winrate=wins/max(1,games)*100; avg=kills/max(1,games)

    canvas=Image.new('RGBA',(W,H),(*BG,255)); draw=ImageDraw.Draw(canvas,'RGBA')
    # Dark Dominion background with restrained violet geometry.
    for yy in range(H):
        t=yy/H; draw.line((0,yy,W,yy),fill=(3+int(5*t),3+int(3*t),10+int(12*t),255))
    glow=Image.new('RGBA',(W,H),(0,0,0,0)); gd=ImageDraw.Draw(glow)
    gd.ellipse((-420,-360,950,700),fill=(*PURPLE,54)); gd.ellipse((1180,700,2300,1750),fill=(*CYAN,20))
    canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(180))); draw=ImageDraw.Draw(canvas,'RGBA')
    for x in range(-180,1900,260): draw.polygon([(x,0),(x+80,0),(x-120,230),(x-190,230)],fill=(*VIOLET,12))

    # Header / identity.
    panel(draw,(44,40,1756,306),34,fill=(9,8,20),outline=PURPLE,width=3)
    draw_brand_mark(draw,76,84,72)
    text(draw,(178,80),'DOMINION FACEIT',31,WHITE,True)
    text(draw,(178,121),'PLAYER NETWORK',15,VIOLET,True)
    avatar=fetch_avatar(avatar_url,210); paste_round(canvas,avatar,(390,68,600,278),35)
    draw.rounded_rectangle((384,62,606,284),radius=41,outline=(*accent,255),width=5)
    text(draw,(640,83),'PLAYER PROFILE',17,VIOLET,True)
    text(draw,(640,120),display_name[:23],48,WHITE,True)
    text(draw,(640,182),f"ID  {player.get('game_id') or 'NOT LINKED'}",20,MUTED,True)
    draw.rounded_rectangle((640,225,910,267),radius=18,fill=(*accent,38),outline=(*accent,190),width=2)
    text(draw,(775,246),f'{league.upper()} LEAGUE',17,accent,True,'mm')
    draw_level_badge(draw,1495,172,level,accent)
    text(draw,(1390,88),f'{points} ELO',28,WHITE,True)
    text(draw,(1710,267),'PLAYER '+str(player['user_id'])[-6:],14,MUTED,True,'ra')

    # Main statistics area.
    panel(draw,(44,340,1188,812),30,fill=PANEL,outline=(55,44,82))
    text(draw,(82,378),'STATISTICS',27,WHITE,True)
    text(draw,(82,418),'Performance based on confirmed Dominion matches',15,MUTED)
    draw_donut(draw,(245,600),112,min(1,kd/2),(*PURPLE,255),(*RED,255),'K / D',f'{kd:.2f}')
    text(draw,(400,492),'KILLS',15,MUTED,True); text(draw,(400,522),kills,37,CYAN,True)
    text(draw,(400,590),'DEATHS',15,MUTED,True); text(draw,(400,620),deaths,37,RED,True)
    stat_cards=[('MATCHES',games),('WINS',wins),('LOSSES',losses),('WIN RATE',f'{winrate:.0f}%'),('AVG KILLS',f'{avg:.1f}'),('ASSISTS',assists),('MVP',mvp),('RATING',f'{(kd*.55+winrate/100*.45):.2f}')]
    for i,(label,value) in enumerate(stat_cards):
        col=i%4; row=i//4; x=570+col*145; y=474+row*132
        draw.rounded_rectangle((x,y,x+128,y+108),radius=18,fill=(*PANEL_2,255),outline=(67,57,91,220),width=1)
        text(draw,(x+14,y+14),label,12,MUTED,True); text(draw,(x+14,y+50),value,27,WHITE,True)

    # Elo and league sidebar.
    panel(draw,(1220,340,1756,640),30,fill=PANEL,outline=accent)
    text(draw,(1258,379),'RANK PROGRESS',22,WHITE,True)
    lvl,floor,next_floor,ratio=elo_bounds(points)
    text(draw,(1258,430),f'LEVEL {lvl}',30,accent,True); text(draw,(1715,432),f'{points} ELO',25,WHITE,True,'ra')
    line_progress(draw,(1258,496,1715,516),ratio,accent)
    text(draw,(1258,532),f'{floor} ELO',14,MUTED,True)
    text(draw,(1715,532),('MAX' if next_floor is None else f'{next_floor} ELO'),14,MUTED,True,'ra')
    remaining='MAXIMUM LEVEL' if next_floor is None else f'{next_floor-points} ELO TO LEVEL {lvl+1}'
    text(draw,(1258,579),remaining,17,CYAN,True)

    panel(draw,(1220,672,1756,812),26,fill=(10,9,21),outline=(55,44,82))
    text(draw,(1258,706),'LEAGUE',14,MUTED,True); text(draw,(1258,738),league,31,accent,True)
    text(draw,(1715,715),'RECORD',14,MUTED,True,'ra'); text(draw,(1715,748),f'{wins}W  {losses}L',24,WHITE,True,'ra')

    # Map performance.
    panel(draw,(44,846,1188,1306),30,fill=PANEL,outline=(55,44,82))
    text(draw,(82,883),'MAP PERFORMANCE',25,WHITE,True)
    text(draw,(82,919),'Recent verified match record',14,MUTED)
    for i,(name,map_wins,map_losses) in enumerate(map_records(recent,player['user_id'])):
        col=i%3; row=i//3; x=82+col*356; y=963+row*148
        draw.rounded_rectangle((x,y,x+326,y+126),radius=20,fill=(*PANEL_2,255),outline=(62,53,84,220),width=1)
        total=map_wins+map_losses; wr=map_wins/max(1,total)*100
        # Simple map emblem in Dominion colors.
        draw.rounded_rectangle((x+18,y+18,x+94,y+108),radius=16,fill=(*PURPLE,35),outline=(*accent,170),width=2)
        text(draw,(x+56,y+63),name[:2].upper(),22,accent,True,'mm')
        text(draw,(x+112,y+20),name,19,WHITE,True)
        text(draw,(x+112,y+55),f'W {map_wins}   L {map_losses}',16,MUTED,True)
        text(draw,(x+112,y+86),f'WIN RATE  {wr:.0f}%',15,CYAN,True)

    # Recent form sidebar.
    panel(draw,(1220,846,1756,1306),30,fill=PANEL,outline=CYAN)
    text(draw,(1258,883),'RECENT MATCHES',25,WHITE,True)
    text(draw,(1258,921),'Last confirmed games',14,MUTED)
    results=[match_result(match,player['user_id']) for match in recent[:20]]; results=[r for r in results if r]
    for i in range(20):
        col=i%5; row=i//5; x=1258+col*88; y=982+row*73
        result=results[i] if i<len(results) else '—'; color=GREEN if result=='W' else RED if result=='L' else (69,64,84)
        draw.rounded_rectangle((x,y,x+64,y+54),radius=13,fill=(*color,35),outline=(*color,235),width=2)
        text(draw,(x+32,y+27),result,20,WHITE,True,'mm')
    form_wins=sum(1 for r in results if r=='W'); form_losses=sum(1 for r in results if r=='L')
    text(draw,(1258,1264),f'FORM  {form_wins}W / {form_losses}L',16,VIOLET,True)

    text(draw,(900,1360),'DOMINION FACEIT  •  RISE  •  ASCEND  •  DOMINATE',16,VIOLET,True,'mm')
    out=io.BytesIO(); canvas.convert('RGB').save(out,'PNG',quality=96); out.seek(0); return out


async def build_profile_card(player,display_name,avatar_url,recent):
    return await asyncio.to_thread(build_profile_card_sync,player,display_name,avatar_url,recent)
