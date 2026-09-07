"""Build the shared neon SVG set from the recorded eight-file example.

Standalone SVG: no scripts, remote fonts or bitmap dependencies. CSS movement is
purely decorative; every relationship and state is legible in the static frame.
"""
from pathlib import Path
import json, math, random, tomllib
from html import escape

ROOT = Path(__file__).resolve().parents[1]
RECORD = json.loads((ROOT / 'docs/demo-results.json').read_text())


def svg(kind, dark, width, height, body, title, desc):
    slug = f'{kind}-{"dark" if dark else "light"}'
    bg, panel, ink, muted, line = ('#050817','#0b122b','#eef2ff','#91a1c6','#23355b') if dark else ('#f5f7ff','#ffffff','#12214a','#53668d','#c5d0ef')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="{slug}-title {slug}-desc">
<title id="{slug}-title">{escape(title)}</title><desc id="{slug}-desc">{escape(desc)}</desc>
<defs><linearGradient id="neon" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="1000" y2="400"><stop stop-color="{"#55c8ff" if dark else "#2875c9"}"/><stop offset=".48" stop-color="{"#7489ff" if dark else "#5855ca"}"/><stop offset="1" stop-color="{"#a879ff" if dark else "#8050c5"}"/></linearGradient>
<radialGradient id="haze"><stop stop-color="{'#4e49c6' if dark else '#b5c1ff'}" stop-opacity=".32"/><stop offset="1" stop-color="{bg}" stop-opacity="0"/></radialGradient>
<filter id="glow" x="-100%" y="-100%" width="300%" height="300%"><feGaussianBlur stdDeviation="4"/></filter>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" fill="none" stroke="{line}" stroke-opacity=".4" stroke-width=".6"/></pattern>
<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="m1 1 7 4-7 4" fill="none" stroke="#829aff" stroke-width="1.5"/></marker></defs>
<style>
text{{font-family:Arial,'Noto Sans',sans-serif;fill:{ink}}}.muted{{fill:{muted}}}.mono{{font-family:ui-monospace,SFMono-Regular,Consolas,monospace}}.panel{{fill:{panel};stroke:{line};stroke-width:1}}.wire{{fill:none;stroke:{line};stroke-width:1.2}}.active{{fill:none;stroke:url(#neon);stroke-width:1.6}}.flow{{fill:none;stroke:#7ed9ff;stroke-width:2;stroke-dasharray:12 240;animation:flow 6s linear infinite}}.glimmer{{animation:glimmer 6s ease-in-out infinite}}.gather{{animation:gather 2.8s cubic-bezier(.2,.7,.2,1) both}}.orbit{{transform-box:fill-box;transform-origin:center;animation:orbit 40s linear infinite}}@keyframes flow{{to{{stroke-dashoffset:-504}}}}@keyframes glimmer{{0%,100%{{opacity:.3}}45%,65%{{opacity:1}}}}@keyframes gather{{from{{opacity:0;transform:translateY(16px) scale(.95)}}to{{opacity:1;transform:none}}}}@keyframes orbit{{to{{transform:rotate(360deg)}}}}@media(prefers-reduced-motion:reduce){{*{{animation:none!important}}.flow{{display:none}}}}
</style><rect width="{width}" height="{height}" rx="16" fill="{bg}"/><rect width="{width}" height="{height}" rx="16" fill="url(#grid)"/><ellipse cx="{width*.58}" cy="{height*.4}" rx="{width*.52}" ry="{height*.72}" fill="url(#haze)"/>
{body}</svg>'''


def text(x,y,s,size=16,cls='',extra=''):
    return f'<text x="{x}" y="{y}" font-size="{size}" class="{cls}" {extra}>{escape(s)}</text>'


def card(x,y,w,h,title,sub='',active=False):
    out=f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" class="panel"/>'
    if active: out+=f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="none" stroke="url(#neon)"/>'
    return out+text(x+20,y+32,title,20,'', 'font-weight="600"')+text(x+20,y+57,sub,13,'muted mono')


def wire(d,active=True):
    return f'<path d="{d}" class="{"active" if active else "wire"}" marker-end="url(#arrow)"/>'+(f'<path d="{d}" class="flow" aria-hidden="true"/>' if active else '')


def particle_field(cx,cy,rx,ry,n=1100):
    rng=random.Random(42); out=[]
    for i in range(n):
        theta=rng.random()*math.tau; z=rng.uniform(-1,1); r=math.sqrt(1-z*z)
        x=cx+rx*r*math.cos(theta); y=cy+ry*z
        depth=(math.sin(theta)+1)/2; opacity=.16+depth*.66
        out.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{.6+depth*.8:.2f}" fill="{"#8d90ff" if x>cx else "#67bfff"}" opacity="{opacity:.2f}"/>')
    return ''.join(out)


def brand():
    body='<g class="gather" aria-hidden="true">'+particle_field(802,235,260,235,1000)+'</g>'
    body+='<g class="gather">'
    body+='<path d="M70 88h32v30h32" class="active" stroke-width="3"/>'
    for i,(x,y) in enumerate([(70,88),(102,103),(134,118)]):body+=f'<circle cx="{x}" cy="{y}" r="5" fill="#8db7ff" class="gather" style="animation-delay:{i*.25}s"/>'
    body+=text(58,200,'DirtyGraph',72,'','font-weight="700" letter-spacing="-3" style="fill:url(#neon)"')
    body+=text(60,251,'Change one thing. Not the whole graph.',23)
    body+=text(62,306,'INCREMENTAL RECOMPUTATION FOR CODE GRAPHS',12,'muted mono','letter-spacing="1.7"')+'</g>'
    body+='<path d="M600 300 689 228 783 260 858 170 938 208" class="active"/><path d="M600 300 689 228 783 260 858 170 938 208" class="flow"/>'
    for x,y in [(600,300),(689,228),(783,260),(858,170),(938,208)]:body+=f'<circle cx="{x}" cy="{y}" r="14" fill="#7697ff" filter="url(#glow)" opacity=".65"/><circle cx="{x}" cy="{y}" r="4" fill="#cbeaff"/>'
    return body


def architecture():
    b=text(40,48,'THE INCREMENTAL ENGINE',12,'muted mono','letter-spacing="2"')+text(40,88,'A small engine. A precise update.',30,'','font-weight="600"')
    # Input layer -> propagation engine -> adapter; state checkpoints form bottom rail.
    for x,label in [(40,'01 / INPUT'),(360,'02 / INVALIDATE'),(680,'03 / RE-DERIVE')]: b+=text(x,140,label,12,'muted mono')
    b+=wire('M300 210H360')+wire('M300 314H360')
    b+=wire('M620 210H680')+wire('M490 278V246')
    b+=wire('M960 210H972Q980 210 980 218V388Q980 396 972 396H592Q584 396 584 404V424')
    b+=wire('M420 424V350',False)
    b+=card(40,174,260,72,'Existing graph','JSON / SQLite')+card(40,278,260,72,'Source files','File-level provenance')
    b+=card(360,174,260,72,'DepGraph + dirty','Reachability · explain why',True)+card(360,278,260,72,'BLAKE3 detection','Content hashes → dirty set')
    b+=card(680,174,280,72,'Rederive engine','Topological calls → adapter',True)
    b+=text(694,293,'DerivedContent → application',14,'mono')+text(694,319,'Application owns output persistence',13,'muted')
    b+=card(360,424,260,72,'Store · sidecar','Hashes · dirty bits')
    b+=text(670,431,'✓ successful calls checkpointed',13,'muted mono')+text(670,456,'↻ failed calls stay dirty',13,'muted mono')
    return b


def process():
    b=text(40,46,'ONE EDIT. A LOCAL CHAIN REACTS.',12,'muted mono','letter-spacing="2"')+text(40,89,'Follow the dependency. Leave the rest.',30,'','font-weight="600"')
    xs=[70,300,530,760]
    for i in range(3):b+=wire(f'M{xs[i]+150} 195H{xs[i+1]}')
    for i,(x,label) in enumerate(zip(xs,RECORD['affected'])):
        b+=text(x,141,f'0{i+1} / '+['DIRECT','PROPAGATED','PROPAGATED','PROPAGATED'][i],12,'muted mono')
        b+=card(x,160,150,72,label, 'source changed' if i==0 else 'dependent',True)
        b+=f'<circle cx="{x+132}" cy="178" r="4" fill="#93b9ff" class="glimmer" style="animation-delay:{i*.8}s"/>'
    b+=wire('M220 323H300',False)
    for x,label in zip(xs,['invoice','billing','search','logger']):b+=card(x,288,150,72,label,'unchanged')
    b+=text(70,402,'DETECT → PROPAGATE → RE-DERIVE ALL 4 → CHECKPOINT',13,'muted mono')
    b+=text(70,438,'8 source files   /   4 affected   /   4 untouched   /   next pass: 0',15,'muted mono')
    return b


def integration():
    b=text(40,46,'FIT INTO THE GRAPH YOU ALREADY HAVE',12,'muted mono','letter-spacing="2"')+text(40,88,'Your graph. Your re-derivation.',30,'','font-weight="600"')
    b+='<ellipse cx="500" cy="280" rx="215" ry="146" class="wire"/><ellipse cx="500" cy="280" rx="177" ry="122" class="wire" stroke-dasharray="2 10"/>'
    b+='<g class="orbit" aria-hidden="true"><ellipse cx="500" cy="280" rx="180" ry="180" fill="none"/><circle cx="500" cy="100" r="4" fill="#819fff"/></g>'
    b+=wire('M288 177H360Q372 177 372 189V236Q372 248 384 248H424')
    b+=wire('M288 280H424')
    b+=wire('M288 383H360Q372 383 372 371V324Q372 312 384 312H424')
    b+=wire('M576 248H616Q628 248 628 236V189Q628 177 640 177H712')
    b+=wire('M576 280H712')
    b+=wire('M576 312H616Q628 312 628 324V371Q628 383 640 383H712')
    b+='<circle cx="500" cy="280" r="91" fill="url(#haze)"/><circle cx="500" cy="280" r="76" class="panel"/><circle cx="500" cy="280" r="76" fill="none" stroke="url(#neon)"/>'
    b+=text(500,276,'DirtyGraph',24,'','text-anchor="middle" font-weight="700"')+text(500,302,'dirty → rederive',12,'muted mono','text-anchor="middle"')
    for x,labels in [(40,[('graphify','node-link JSON'),('code-review-graph','SQLite'),('Manual wiring','add / link')]),(712,[('Local','echo / codegraph'),('OpenAI-compatible','DeepSeek / Qwen config'),('Custom adapter','Python · re_derive()')])]:
        for y,(a,c) in zip([141,244,347],labels):b+=card(x,y,248,72,a,c)
    return b


def scene():
    b=particle_field(500,380,470,290,2200)
    b+='<path d="M204 416 330 326 497 378 655 286 793 364" class="active"/>'
    for x,y in [(204,416),(330,326),(497,378),(655,286),(793,364)]:b+=f'<circle cx="{x}" cy="{y}" r="20" fill="#719cff" filter="url(#glow)" opacity=".7"/><circle cx="{x}" cy="{y}" r="4" fill="#d9f3ff"/>'
    return b


def demo(step):
    xs=[32,218,404,590]; b=''
    for row,names in enumerate([RECORD['affected'],['invoice','billing','search','logger']]):
        y=52+row*132
        for i,x in enumerate(xs):
            if (row==0 and i<3) or (row==1 and i==0):b+=wire(f'M{x+140} {y+36}H{xs[i+1]}',row==0 and step in [1,2])
            b+=card(x,y,140,72,names[i],['clean','dirty','updated','clean'][step] if row==0 else 'unchanged',row==0 and step in [1,2])
    return b


for dark in (False,True):
    theme='dark' if dark else 'light'
    for kind,wh,fn,title,desc in [
        ('hero',(1000,380),brand,'DirtyGraph','Change one thing. Recompute only the affected graph nodes.'),
        ('architecture',(1000,540),architecture,'DirtyGraph architecture','Existing graph and source hashes feed dependency propagation, then ordered adapters; Store checkpoints successful calls. Applications persist derived content.'),
        ('process',(1000,480),process,'Incremental recomputation','Editing auth affects auth, session, views and api. Invoice, billing, search and logger remain unchanged; a second pass updates zero nodes.'),
        ('integrations',(1000,470),integration,'Supported integration paths','Read graphify JSON, code-review-graph SQLite or manually registered nodes. Re-derive with local, optional OpenAI-compatible, or custom Python adapters.'),
        ('scene',(1000,700),scene,'Particle graph','A local change lights one dependency chain while the rest of the graph stays quiet.')]:
        content=svg(kind,dark,*wh,fn(),title,desc)
        for folder in [ROOT/'assets',ROOT/'web/assets']:
            folder.mkdir(exist_ok=True,parents=True);(folder/f'{kind}-{theme}.svg').write_text(content)
    for step in range(4):
        (ROOT/f'web/assets/demo-{step}-{theme}.svg').write_text(svg(f'demo-{step}',dark,760,300,demo(step),'Eight-file example',RECORD['steps'][step]['output']))

print('Generated 4 animated figure pairs, scene fallbacks and recorded demo states.')

# Local badges do not depend on external image services; CI is a workflow link,
# not a cached claim that the latest build passed.
for name,left,right in [('license','license','Apache-2.0'),('python','Python','3.12+'),('ci','CI','GitHub Actions'),('release','release','v'+tomllib.loads((ROOT/'pyproject.toml').read_text())['project']['version'])]:
    lw=len(left)*7+18; rw=len(right)*7+20; w=lw+rw
    (ROOT/f'assets/badge-{name}.svg').write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="22" role="img" aria-label="{left}: {right}"><rect width="{w}" height="22" rx="3" fill="#292f4b"/><path d="M{lw} 0H{w-3}Q{w} 0 {w} 3V19Q{w} 22 {w-3} 22H{lw}Z" fill="#635bb1"/><g fill="#fff" font-family="Arial,sans-serif" font-size="11" text-anchor="middle"><text x="{lw/2}" y="15">{left}</text><text x="{lw+rw/2}" y="15">{right}</text></g></svg>')

# Narrow figures keep labels readable in GitHub's mobile README column.
def mobile_card(x,y,w,title,sub):
    return (f'<rect x="{x}" y="{y}" width="{w}" height="72" rx="10" class="panel"/>'
            +text(x+16,y+28,title,17,'','font-weight="600"')+text(x+16,y+52,sub,12,'muted mono'))

def mobile_figure(kind):
    if kind=='hero':
        b=particle_field(326,216,172,150,420)
        b+=text(28,58,'DIRTYGRAPH / OPEN SOURCE',10,'muted mono')+text(24,120,'DirtyGraph',46,'','font-weight="700" style="fill:url(#neon)"')
        b+=text(28,166,'Change one thing.',18)+text(28,191,'Not the whole graph.',18)
        b+='<path d="M210 248 270 220 324 248 370 204" class="active"/><path d="M210 248 270 220 324 248 370 204" class="flow"/>'
        return 300,b
    if kind=='architecture':
        b=text(24,42,'THE INCREMENTAL ENGINE',11,'muted mono')+text(24,80,'From edit to update.',27,'','font-weight="600"')
        b+=wire('M106 188V244')+wire('M294 188V244')+wire('M212 280H188')+wire('M106 316V388')
        b+=wire('M106 460V524')+wire('M294 460V524')
        b+=mobile_card(24,116,164,'Existing graph','JSON / SQLite')+mobile_card(212,116,164,'Source files','File provenance')
        b+=mobile_card(24,244,164,'DepGraph + dirty','Dirty closure')+mobile_card(212,244,164,'BLAKE3','Hash detection')
        b+=mobile_card(24,388,352,'Rederive engine','Topological calls → adapter')
        b+=mobile_card(24,524,164,'Store','Checkpoints')+mobile_card(212,524,164,'Application','Persist results')
        b+=text(24,646,'Store supplies hashes for the next check.',14,'muted')+text(24,674,'Failed adapter calls leave nodes dirty.',14,'muted')
        return 712,b
    if kind=='process':
        b=text(24,42,'ONE EDIT. A LOCAL CHAIN.',11,'muted mono')+text(24,80,'4 affected. 4 untouched.',26,'','font-weight="600"')
        for i in range(3):b+=wire(f'M99 {188+i*110}V{226+i*110}')
        b+=wire('M301 188V226',False)
        for i,(a,u) in enumerate(zip(RECORD['affected'],['invoice','billing','search','logger'])):
            y=116+i*110
            b+=mobile_card(24,y,150,a,'direct' if i==0 else 'propagated')+mobile_card(226,y,150,u,'unchanged')
            b+=f'<circle cx="158" cy="{y+17}" r="4" fill="#91baff" class="glimmer" style="animation-delay:{i*.8}s"/>'
        b+=text(24,564,'Re-derive all 4 → checkpoint.',16)+text(24,596,'Next pass, with no new edit: 0.',14,'muted')
        return 634,b
    b=text(24,42,'CAPABILITIES / CONNECTIONS',11,'muted mono')+text(24,80,'Your graph. Your workflow.',25,'','font-weight="600"')
    b+='<ellipse cx="200" cy="384" rx="165" ry="116" class="wire"/><ellipse cx="200" cy="384" rx="145" ry="96" class="wire" stroke-dasharray="2 8"/>'
    b+=wire('M200 244V324')+wire('M200 444V524')
    b+='<rect x="24" y="116" width="352" height="128" rx="12" class="panel"/>'
    b+=text(44,148,'INPUTS',12,'muted mono')+text(44,180,'graphify JSON · CRG SQLite',18)+text(44,211,'Manual nodes & edges: add / link',14,'muted')
    b+='<circle cx="200" cy="384" r="61" class="panel" stroke="url(#neon)"/><circle cx="200" cy="384" r="61" class="active"/>'
    b+=text(200,389,'DirtyGraph',21,'','text-anchor="middle" font-weight="700"')
    b+='<rect x="24" y="524" width="352" height="180" rx="12" class="panel"/>'
    b+=text(44,556,'ADAPTER ROUTES',12,'muted mono')+text(44,589,'Local · echo / codegraph',17)+text(44,625,'Compatible · DeepSeek / Qwen',17)+text(44,661,'Custom · Python re_derive()',17)
    return 740,b

for dark in (False,True):
    theme='dark' if dark else 'light'
    for kind in ['hero','architecture','process','integrations']:
        h,b=mobile_figure(kind)
        asset=svg(f'{kind}-mobile',dark,400,h,b,f'DirtyGraph {kind}',f'Narrow layout of the {kind} figure; all relationships remain visible.')
        for folder in [ROOT/'assets',ROOT/'web/assets']:(folder/f'{kind}-mobile-{theme}.svg').write_text(asset)
