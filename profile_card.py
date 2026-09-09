import asyncio, io, math
from pathlib import Path
import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
from elo_levels import elo_bounds, elo_level

ROOT=Path(__file__).resolve().parent
W=H=1600
BG=(3,2,10); PANEL=(14,8,31); CARD=(28,14,53); PURPLE=(172,59,255); PINK=(236,62,183); CYAN=(92,205,245); WHITE=(248,246,252); MUTED=(174,154,194); GREEN=(57,218,142); RED=(242,72,126)
MAPS=('Sandstone','Rust','Province','Prison','Hanami','Dune','Breeze')
LEAGUE_NAMES={'Default':'Default League','Qualifications':'Dominion Rise','Division':'Dominion Ascend','Pro':'Pro League'}

def font(n,b=False):
 p=ROOT/'assets'/'fonts'/('Arial-Bold.ttf' if b else 'Arial.ttf'); return ImageFont.truetype(str(p),n)
def txt(d,xy,s,n,c=WHITE,b=False,a=None): d.text(xy,str(s),font=font(n,b),fill=(*c,255),anchor=a)
def box(d,b,r=24,fill=PANEL,outline=(84,33,126),w=2):
 x1,y1,x2,y2=b; d.rounded_rectangle((x1+8,y1+9,x2+8,y2+9),r,fill=(0,0,0,105)); d.rounded_rectangle(b,r,fill=(*fill,246),outline=(*outline,225),width=w)
def avatar(url,size):
 try:
  q=requests.get(str(url),timeout=8); q.raise_for_status(); return ImageOps.fit(Image.open(io.BytesIO(q.content)).convert('RGB'),(size,size),Image.Resampling.LANCZOS)
 except Exception:
  im=Image.new('RGB',(size,size),(19,10,35)); z=ImageDraw.Draw(im); z.ellipse((size*.28,size*.13,size*.72,size*.57),fill=(92,52,121)); z.rounded_rectangle((size*.17,size*.56,size*.83,size*.96),int(size*.2),fill=(92,52,121)); return im
def paste_round(im,pic,b,r):
 x1,y1,x2,y2=b; pic=ImageOps.fit(pic,(x2-x1,y2-y1),Image.Resampling.LANCZOS); m=Image.new('L',pic.size); ImageDraw.Draw(m).rounded_rectangle((0,0,pic.width-1,pic.height-1),r,fill=255); im.paste(pic,(x1,y1),m)
def league(points): return 'Pro' if points>=1600 else 'Division' if points>=1350 else 'Qualifications' if points>=1150 else 'Default'
def result(m,uid):
 if m.get('score_a') is None or m.get('score_b') is None:return None
 ina=str(uid) in str(m.get('team_a','')).split(','); wa=int(m['score_a'])>int(m['score_b']); return 'W' if ina==wa else 'L'
def map_rows(recent,uid):
 out={m:[0,0] for m in MAPS}
 for row in recent:
  name=str(row.get('map') or ''); r=result(row,uid)
  if name in out and r: out[name][0 if r=='W' else 1]+=1
 used=[(m,*v) for m,v in out.items() if sum(v)]; empty=[(m,0,0) for m,v in out.items() if not sum(v)]; return (used+empty)[:6]
def donut(d,c,r,ratio,value,label):
 x,y=c; d.arc((x-r,y-r,x+r,y+r),-90,270,fill=(59,35,82),width=17); d.arc((x-r,y-r,x+r,y+r),-90,-90+360*max(0,min(1,ratio)),fill=PURPLE,width=17); txt(d,(x,y-5),value,36,WHITE,True,'mm'); txt(d,(x,y+34),label,13,MUTED,True,'mm')
def hexagon(d,c,r,value):
 x,y=c; pts=[(x+r*math.cos(math.radians(i*60-30)),y+r*math.sin(math.radians(i*60-30))) for i in range(6)]; d.polygon(pts,fill=(18,7,37),outline=(*PURPLE,255)); pts2=[(x+(r-10)*math.cos(math.radians(i*60-30)),y+(r-10)*math.sin(math.radians(i*60-30))) for i in range(6)]; d.line(pts2+[pts2[0]],fill=(*PINK,255),width=4); txt(d,(x,y),value,39,WHITE,True,'mm')

def build_profile_card_sync(player,name,avatar_url,recent,meta=None):
 meta=meta or {}; points=max(0,int(player.get('points',0))); lvl=elo_level(points); lg=meta.get('league') or league(points)
 games=int(player.get('games',0)); wins=int(player.get('wins',0)); losses=int(player.get('losses',max(0,games-wins))); kills=int(player.get('kills',0)); deaths=int(player.get('deaths',0)); assists=int(player.get('assists',0)); mvp=int(player.get('mvp',0))
 kd=kills/max(1,deaths); wr=wins/max(1,games)*100; avg=kills/max(1,games); rounds=max(1,games*20); kpr=kills/rounds; apr=assists/rounds; rating=kd*.55+wr/100*.45; impact=max(0,2.13*kpr+.42*apr-.41); svr=max(0,min(100,(1-deaths/rounds)*100))
 im=Image.new('RGBA',(W,H),(*BG,255)); d=ImageDraw.Draw(im,'RGBA')
 glow=Image.new('RGBA',(W,H)); g=ImageDraw.Draw(glow); g.ellipse((-380,-250,920,760),fill=(*PURPLE,72)); g.ellipse((900,650,1900,1700),fill=(*PINK,35)); im.alpha_composite(glow.filter(ImageFilter.GaussianBlur(160))); d=ImageDraw.Draw(im,'RGBA')
 # Header exactly follows the supplied dashboard composition.
 box(d,(25,25,1575,220),26,fill=(18,6,39),outline=PURPLE,w=3); paste_round(im,avatar(avatar_url,150),(52,48,202,198),25); d.rounded_rectangle((48,44,206,202),27,outline=(*PURPLE,255),width=4)
 txt(d,(235,63),'#'+str(player['user_id'])[-5:],18,PURPLE,True); txt(d,(235,92),name[:24],41,WHITE,True); txt(d,(235,149),f"ID: {player.get('game_id') or '—'}",18,MUTED,True); txt(d,(1515,73),'DOMINION FACEIT',22,PURPLE,True,'ra')
 # Main statistics.
 box(d,(25,250,985,900),28); txt(d,(62,285),'STATISTIC',24,WHITE,True)
 d.rounded_rectangle((55,340,475,565),22,fill=(*CARD,255)); donut(d,(180,452),88,min(1,kd/2),f'{kd:.2f}','K / D'); txt(d,(295,402),'KILL / DEATHS',16,MUTED,True); txt(d,(295,448),f'K = {kills}',22,CYAN,True); txt(d,(295,486),f'D = {deaths}',22,PINK,True)
 d.rounded_rectangle((500,340,950,565),22,fill=(*CARD,255)); txt(d,(535,378),'ELO',16,MUTED,True); hexagon(d,(862,418),64,lvl); lo,hi,nxt,ratio=elo_bounds(points); txt(d,(535,423),points,35,WHITE,True); d.rounded_rectangle((535,490,915,506),8,fill=(70,42,92)); d.rounded_rectangle((535,490,535+int(380*ratio),506),8,fill=(*PURPLE,255)); txt(d,(535,520),lo,13,MUTED,True); txt(d,(915,520),nxt or 'MAX',13,MUTED,True,'ra')
 metrics=[('RATING',f'{rating:.2f}'),('AVG',f'{avg:.2f}'),('IMPACT',f'{impact:.2f}'),('KPR',f'{kpr:.2f}'),('ASSISTS',assists),('SVR',f'{svr:.0f}%')]
 for i,(lab,val) in enumerate(metrics):
  col=i%3; row=i//3; x=55+col*300; y=595+row*137; d.rounded_rectangle((x,y,x+275,y+112),20,fill=(*CARD,255),outline=(98,35,139,180),width=1); txt(d,(x+20,y+20),lab,14,MUTED,True); txt(d,(x+250,y+22),val,25,WHITE,True,'ra'); d.rounded_rectangle((x+20,y+70,x+250,y+80),5,fill=(64,38,81)); d.rounded_rectangle((x+20,y+70,x+105,y+80),5,fill=(*PURPLE,255)); txt(d,(x+20,y+89),'DOMINION',11,PURPLE,True)
 # Right mini form and player information.
 box(d,(1015,250,1575,385),24); forms=[result(x,player['user_id']) for x in recent[:6]]
 for i in range(6):
  r=forms[i] if i<len(forms) and forms[i] else ''; c=GREEN if r=='W' else RED if r=='L' else PURPLE; x=1047+i*85; fill=(*c,40) if r else (*CARD,255); d.rounded_rectangle((x,283,x+66,351),16,fill=fill,outline=(*c,230),width=2); txt(d,(x+33,317),r,20,WHITE,True,'mm')
 box(d,(1015,410,1575,650),25); play=f'{games*20//60}h'; joined=meta.get('joined_date') or '—'; infos=[('PLAYTIME',play),('JOIN DATE',joined),('GAMES',games),('MVP',mvp)]
 for i,(lab,val) in enumerate(infos):
  x=1055+(i%2)*260; y=450+(i//2)*95; txt(d,(x,y),lab,14,MUTED,True); txt(d,(x,y+32),val,28,WHITE,True)
 # League/places block.
 box(d,(1015,675,1575,1090),25); txt(d,(1055,715),'LEAGUE',15,MUTED,True); txt(d,(1055,748),LEAGUE_NAMES[lg],31,PURPLE,True); txt(d,(1055,800),'PLACES',16,WHITE,True)
 tops=meta.get('league_top') or []
 for i in range(3):
  y=850+i*62; txt(d,(1055,y+25),f'#{i+1}',18,WHITE,True); item=tops[i] if i<len(tops) else {}; paste_round(im,avatar(item.get('avatar_url',''),42),(1110,y+5,1152,y+47),11); txt(d,(1170,y+25),item.get('name','—')[:20],18,WHITE,True,'lm')
 d.line((1055,1038,1528,1038),fill=(*MUTED,100),width=2); txt(d,(1055,1062),f"#{meta.get('position','—')}",18,PURPLE,True); txt(d,(1170,1062),name[:20],18,WHITE,True)
 # Map section.
 box(d,(25,925,985,1570),28); txt(d,(62,960),'MAP STATISTIC',24,WHITE,True); rows=map_rows(recent,player['user_id']); best=max(rows,key=lambda z:(z[1]/max(1,z[1]+z[2]),z[1]))
 donut(d,(175,1115),92,best[1]/max(1,best[1]+best[2]),f'{best[1]/max(1,best[1]+best[2])*100:.0f}%','WIN RATE'); txt(d,(305,1062),best[0],25,WHITE,True); txt(d,(305,1110),f'W = {best[1]}   L = {best[2]}',19,MUTED,True)
 for i,(mn,mw,ml) in enumerate(rows):
  col=i%3; row=i//3; x=55+col*300; y=1240+row*137; d.rounded_rectangle((x,y,x+275,y+115),19,fill=(*CARD,255),outline=(91,34,130,180),width=1); d.rounded_rectangle((x+15,y+15,x+82,y+82),14,outline=(*PURPLE,240),width=3); txt(d,(x+48,y+49),mn[:2].upper(),17,PURPLE,True,'mm'); txt(d,(x+98,y+20),mn,17,WHITE,True); txt(d,(x+98,y+49),f'W {mw}   L {ml}',14,MUTED,True); txt(d,(x+18,y+94),f'K/D {kd:.2f}',13,WHITE,True); txt(d,(x+250,y+94),f'W/R {mw/max(1,mw+ml)*100:.0f}%',13,CYAN,True,'ra')
 # Recent match grid.
 box(d,(1015,1115,1575,1570),25); txt(d,(1055,1150),'RECENT MATCHES',23,WHITE,True); rr=[result(x,player['user_id']) for x in recent[:28]]; rr=[x for x in rr if x]
 for i in range(28):
  col=i%7; row=i//7; x=1055+col*69; y=1210+row*73; r=rr[i] if i<len(rr) else ''; c=GREEN if r=='W' else RED if r=='L' else PURPLE; fill=(*c,25) if r else (*CARD,255); d.rounded_rectangle((x,y,x+54,y+54),13,fill=fill,outline=(*c,220),width=2); txt(d,(x+27,y+27),r,17,WHITE,True,'mm')
 out=io.BytesIO(); im.convert('RGB').save(out,'PNG',quality=96); out.seek(0); return out

async def build_profile_card(player,display_name,avatar_url,recent,meta=None): return await asyncio.to_thread(build_profile_card_sync,player,display_name,avatar_url,recent,meta)
