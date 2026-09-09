import io
from pathlib import Path
import requests
from PIL import Image,ImageDraw,ImageFilter,ImageFont,ImageOps
from elo_levels import elo_level
W=H=1400; ROOT=Path(__file__).resolve().parent
BG=(3,2,9); PANEL=(16,9,29); CELL=(24,14,40); PURPLE=(171,60,255); PINK=(239,57,175); WHITE=(249,247,252); MUTED=(173,155,190)
LEAGUE_NAMES={'Default':'DEFAULT LEAGUE','Qualifications':'DOMINION RISE','Division':'DOMINION ASCEND','Pro':'PRO LEAGUE'}
def font(n,b=False):
 p=ROOT/'assets'/'fonts'/('Arial-Bold.ttf' if b else 'Arial.ttf'); return ImageFont.truetype(str(p),n)
def av(url,n):
 try:r=requests.get(str(url),timeout=7);r.raise_for_status();return ImageOps.fit(Image.open(io.BytesIO(r.content)).convert('RGB'),(n,n),Image.Resampling.LANCZOS)
 except Exception:return Image.new('RGB',(n,n),(54,34,72))
def paste(im,pic,xy,n):
 m=Image.new('L',(n,n));ImageDraw.Draw(m).rounded_rectangle((0,0,n-1,n-1),14,fill=255);im.paste(pic,xy,m)
def txt(d,xy,s,n,c=WHITE,b=False,a=None):d.text(xy,str(s),font=font(n,b),fill=(*c,255),anchor=a)
def build_leaderboard(rows,league):
 im=Image.new('RGBA',(W,H),(*BG,255));d=ImageDraw.Draw(im,'RGBA');gl=Image.new('RGBA',(W,H));g=ImageDraw.Draw(gl);g.ellipse((750,-300,1650,750),fill=(*PURPLE,70));g.ellipse((-400,800,650,1700),fill=(*PINK,30));im.alpha_composite(gl.filter(ImageFilter.GaussianBlur(170)));d=ImageDraw.Draw(im,'RGBA')
 txt(d,(48,58),'ЛУЧШИЕ',55,PURPLE,True);txt(d,(365,58),'ИГРОКИ',55,WHITE,True);txt(d,(1348,70),'Dominion Faceit',23,WHITE,True,'ra')
 d.rounded_rectangle((48,150,335,215),18,fill=(*CELL,245));txt(d,(75,183),'LEAGUE:',17,PURPLE,True);txt(d,(315,183),LEAGUE_NAMES.get(league,league).upper(),17,WHITE,True,'ra')
 headers=[('#',65),('PLAYER',160),('WIN',575),('LOSE',700),('WINRATE',825),('POINTS',965),('K/D',1090),('AVG',1200),('',1305)]
 for label,x in headers:txt(d,(x,270),label,15,PURPLE if label not in ('LOSE','WINRATE') else PINK if label=='LOSE' else WHITE,True,'mm')
 for i in range(10):
  y=305+i*94; item=rows[i] if i<len(rows) else None
  d.rounded_rectangle((45,y,1355,y+76),18,fill=(*PANEL,235),outline=(*PURPLE,135),width=2)
  d.rounded_rectangle((45,y,112,y+76),18,fill=(*CELL,255));txt(d,(78,y+38),i+1,22,WHITE,True,'mm')
  if not item:continue
  paste(im,av(item.get('avatar_url'),52),(132,y+12),52);txt(d,(202,y+29),str(item.get('name','Player'))[:22],20,WHITE,True,'lm');txt(d,(202,y+55),f"A {int(item.get('assists',0))} • MVP {int(item.get('mvp',0))}",11,MUTED,True,'lm')
  games=max(0,int(item.get('games',0)));wins=max(0,int(item.get('wins',0)));losses=max(0,int(item.get('losses',games-wins)));kills=max(0,int(item.get('kills',0)));deaths=max(0,int(item.get('deaths',0)));kd=kills/max(1,deaths);avg=kills/max(1,games);wr=wins/max(1,games)*100
  vals=[(wins,575,PURPLE),(losses,700,PINK),(f'{wr:.0f}%',825,WHITE),(int(item.get('points',0)),965,PURPLE),(f'{kd:.2f}',1090,WHITE),(f'{avg:.1f}',1200,WHITE),(elo_level(int(item.get('points',0))),1305,PURPLE)]
  for v,x,c in vals:
   half=38 if x==1305 else 46
   d.rounded_rectangle((x-half,y+10,x+half,y+66),14,fill=(*CELL,220),outline=(*c,180),width=2);txt(d,(x,y+38),v,17,c,True,'mm')
 txt(d,(700,1345),'DOMINION FACEIT  •  COMPETITIVE RANKING',15,PURPLE,True,'mm');out=io.BytesIO();im.convert('RGB').save(out,'PNG',quality=96);out.seek(0);return out
