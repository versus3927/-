import io
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

ROOT=Path(__file__).resolve().parent
W=1080
LEAGUE_DISPLAY_NAMES={"Default":"Default League","Qualifications":"Dominion Rise","Division":"Dominion Ascend","Pro":"Pro League","PC":"PC"}
LEAGUE_COLORS={
 "Default":((100,116,139),(56,189,248)),
 "Qualifications":((250,204,21),(34,197,94)),
 "Division":((168,85,247),(236,72,153)),
 "Pro":((239,68,68),(249,115,22)),
 "PC":((56,189,248),(129,140,248)),
}


def font(size,bold=False):
 path=ROOT/'assets'/'fonts'/('Arial-Bold.ttf' if bold else 'Arial.ttf')
 return ImageFont.truetype(str(path),size)


def build_matches_card(matches,title='ПОСЛЕДНИЕ МАТЧИ',subtitle='История игр DOMINION FACEIT'):
 """matches: list of dicts with id, league, map, score_a, score_b, status."""
 rows=list(matches)[:10]
 height=270+max(1,len(rows))*104+90
 primary,secondary=(168,85,247),(56,189,248)
 base=Image.new('RGB',(W,height),(5,7,14)); d=ImageDraw.Draw(base)
 for y in range(height):
  t=y/height
  d.line((0,y,W,y),fill=(int(8+22*(1-t)),int(9+12*(1-t)),int(20+30*t)))
 glow=Image.new('RGBA',(W,height),(0,0,0,0)); gd=ImageDraw.Draw(glow)
 gd.ellipse((-280,-260,640,520),fill=(*primary,90))
 gd.ellipse((620,height-560,1400,height+180),fill=(*secondary,60))
 glow=glow.filter(ImageFilter.GaussianBlur(130))
 canvas=base.convert('RGBA'); canvas.alpha_composite(glow); d=ImageDraw.Draw(canvas,'RGBA')
 for x in range(-140,1220,175):
  d.polygon([(x,0),(x+72,0),(x-86,232),(x-142,232)],fill=(*primary,26))
 # Header
 d.rounded_rectangle((30,28,1050,206),radius=30,fill=(14,16,28,235),outline=(*primary,190),width=3)
 d.text((64,60),'DOMINION FACEIT',font=font(21,True),fill=(*primary,255))
 d.text((64,95),title,font=font(41,True),fill=(246,247,252,255))
 d.text((64,153),subtitle,font=font(19),fill=(169,172,190,255))
 # 3D controller-like badge
 for off,col in [(14,(0,0,0)),(8,secondary),(0,primary)]:
  d.rounded_rectangle((858+off,52+off,1000+off,182+off),radius=32,fill=(*col,215) if col!=(0,0,0) else (0,0,0,130),outline=(255,255,255,80),width=2)
 d.rounded_rectangle((884,96,974,140),radius=18,fill=(20,22,36,255),outline=(255,255,255,120),width=2)
 d.ellipse((896,110,912,126),fill=(255,255,255,235)); d.ellipse((946,110,962,126),fill=(255,255,255,235))
 if not rows:
  d.text((540,height//2),'Матчей пока нет',font=font(30,True),anchor='mm',fill=(226,228,238,255))
 for index,match in enumerate(rows):
  league=str(match.get('league') or 'Default')
  accent,accent2=LEAGUE_COLORS.get(league,LEAGUE_COLORS['Default'])
  y=250+index*104
  d.rounded_rectangle((42+8,y+10,1038+8,y+96),radius=22,fill=(0,0,0,110))
  d.rounded_rectangle((42,y,1038,y+86),radius=22,fill=(19,21,34,240),outline=(*accent,150),width=2)
  d.rounded_rectangle((42,y,54,y+86),radius=8,fill=(*accent,255))
  d.text((78,y+18),f"МАТЧ #{match.get('id','?')}",font=font(24,True),fill=(245,246,250,255))
  d.text((78,y+53),f"{LEAGUE_DISPLAY_NAMES.get(league,league)} · {match.get('map') or 'карта не выбрана'}",font=font(19),fill=(166,170,190,255))
  a,b=match.get('score_a'),match.get('score_b')
  if a is None or b is None:
   status_text,status_color='ИДЁТ',(250,204,21)
   score='? : ?'
  else:
   status_text,status_color='ЗАВЕРШЁН',(70,190,125)
   score=f'{a} : {b}'
  d.rounded_rectangle((640,y+20,822,y+66),radius=14,fill=(28,30,46,255),outline=(*accent2,180),width=2)
  d.text((731,y+43),score,font=font(27,True),anchor='mm',fill=(255,255,255,255))
  d.rounded_rectangle((848,y+24,1014,y+62),radius=13,fill=(24,26,40,255),outline=(*status_color,225),width=2)
  d.text((931,y+44),status_text,font=font(17,True),anchor='mm',fill=(*status_color,255))
 d.text((540,height-44),'DOMINION CYBER • FACEIT STANDOFF 2',font=font(17,True),anchor='mm',fill=(*primary,205))
 out=io.BytesIO(); canvas.convert('RGB').save(out,'PNG',quality=95); out.seek(0); return out
