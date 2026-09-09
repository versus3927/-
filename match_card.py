import io
from pathlib import Path
import requests
from PIL import Image,ImageDraw,ImageFilter,ImageFont,ImageOps
from elo_levels import elo_level
ROOT=Path(__file__).resolve().parent
W,H=1536,864; PURPLE=(171,58,255); PINK=(238,59,178); WHITE=(247,244,252); MUTED=(172,151,190); BG=(3,2,10); PANEL=(16,7,34); CARD=(30,12,53)
LEAGUE_DISPLAY_NAMES={"Default":"Default League","Qualifications":"Dominion Rise","Division":"Dominion Ascend","Pro":"Pro League","PC":"PC"}
def font(n,b=False):
 p=ROOT/'assets'/'fonts'/('Arial-Bold.ttf' if b else 'Arial.ttf'); return ImageFont.truetype(str(p),n)
def txt(d,xy,s,n,c=WHITE,b=False,a=None):d.text(xy,str(s),font=font(n,b),fill=(*c,255),anchor=a)
def box(d,b,r=20,fill=PANEL,outline=(80,27,122),w=2):d.rounded_rectangle(b,r,fill=(*fill,245),outline=(*outline,220),width=w)
def av(url,n):
 try:r=requests.get(str(url),timeout=6);r.raise_for_status();return ImageOps.fit(Image.open(io.BytesIO(r.content)).convert('RGB'),(n,n),Image.Resampling.LANCZOS)
 except:return Image.new('RGB',(n,n),(64,33,86))
def paste(im,pic,xy,n):
 m=Image.new('L',(n,n));ImageDraw.Draw(m).rounded_rectangle((0,0,n-1,n-1),15,fill=255);im.paste(pic,xy,m)
def player_row(im,d,item,x,y,w):
 box(d,(x,y,x+w,y+104),16,fill=CARD);paste(im,av(item.get('avatar_url'),58),(x+18,y+16),58);txt(d,(x+92,y+24),item.get('name','Player')[:18],17,WHITE,True);lvl=elo_level(int(item.get('points',0)));txt(d,(x+92,y+61),lvl,26,PURPLE,True);games=int(item.get('games',0));kd=int(item.get('kills',0))/max(1,int(item.get('deaths',0)));avg=int(item.get('kills',0))/max(1,games);txt(d,(x+175,y+62),f'MATCHES  {games}',11,MUTED,True);txt(d,(x+285,y+62),f'K/D  {kd:.2f}',11,MUTED,True);txt(d,(x+380,y+62),f'AVG  {avg:.1f}',11,MUTED,True)
def build_match_card(match_id,league,map_name,host_name,host_id,team_a,team_b):
 league_display=LEAGUE_DISPLAY_NAMES.get(str(league),str(league))
 im=Image.new('RGBA',(W,H),(*BG,255));d=ImageDraw.Draw(im,'RGBA');gl=Image.new('RGBA',(W,H));g=ImageDraw.Draw(gl);g.ellipse((380,-250,1180,650),fill=(*PURPLE,70));g.rectangle((0,0,W,H),outline=(*PURPLE,70),width=10);im.alpha_composite(gl.filter(ImageFilter.GaussianBlur(90)));d=ImageDraw.Draw(im,'RGBA');d.rounded_rectangle((7,18,W-7,H-18),22,outline=(*PURPLE,190),width=2)
 box(d,(28,70,475,793),20);box(d,(1061,70,1508,793),20);txt(d,(45,84),'COUNTER-TERRORISTS',15,WHITE,True);txt(d,(1078,84),'TERRORISTS',15,WHITE,True)
 for i in range(5):
  player_row(im,d,team_a[i] if i<len(team_a) else {},43,120+i*128,417);player_row(im,d,team_b[i] if i<len(team_b) else {},1076,120+i*128,417)
 box(d,(500,70,1037,123),15);txt(d,(525,96),f'MATCH #{match_id}',17,PURPLE,True);txt(d,(1012,96),str(map_name).upper(),17,WHITE,True,'ra')
 box(d,(500,142,1037,196),15);txt(d,(525,169),str(league_display).upper(),16,WHITE,True);txt(d,(760,169),'BO1',15,PURPLE,True,'mm');txt(d,(1012,169),'10 PLAYERS',15,MUTED,True,'ra')
 box(d,(500,218,1037,378),18);txt(d,(530,246),'HOST',13,MUTED,True);txt(d,(530,281),host_name[:22],22,WHITE,True);txt(d,(760,246),'ID',13,MUTED,True);txt(d,(760,281),host_id or '—',21,PURPLE,True);txt(d,(530,333),'Нажмите кнопку под карточкой, чтобы получить ID хоста',13,MUTED)
 box(d,(500,408,1037,489),17);txt(d,(525,432),'AVERAGE ELO',13,MUTED,True);avg=sum(int(p.get('points',0)) for p in team_a+team_b)//max(1,len(team_a)+len(team_b));txt(d,(768,448),avg,25,WHITE,True,'mm');d.rounded_rectangle((525,467,1010,477),5,fill=(77,33,96));d.rounded_rectangle((525,467,525+int(485*min(1,avg/2000)),477),5,fill=(*PURPLE,255))
 box(d,(500,512,1037,793),18);txt(d,(530,540),'MATCH INFORMATION',15,PURPLE,True);txt(d,(530,585),f'League: {league_display}',18,WHITE,True);txt(d,(530,622),f'Map: {map_name}',18,WHITE,True);txt(d,(530,659),'Format: first to 13 rounds',18,WHITE,True);txt(d,(530,696),f'Host: {host_name}',18,WHITE,True)
 txt(d,(768,829),'DOMINION FACEIT',21,PURPLE,True,'mm');out=io.BytesIO();im.convert('RGB').save(out,'PNG',quality=96);out.seek(0);return out
